"""ATM IV Percentile (IVP) — a display-only diagnostic for the daily scan.

IVP answers "how high is this stock's current at-the-money implied volatility
relative to its own last year?". It is **informational only**: nothing here
feeds cointegration, ranking, Monte Carlo, half-life, capital, or option-leg
selection. The scanner attaches it to the result rows and the scan is otherwise
byte-for-byte unchanged.

Definition
----------
    IVP = 100 * count(prior valid ATM-IV obs < current ATM IV)
              / count(prior valid ATM-IV obs)

over up to ``IVP_LOOKBACK_TRADING_DAYS`` prior observations. This is a genuine
IV percentile - not India VIX, not historical vol, not the NSE daily-volatility
report, not IV rank, not IV/HV, and not a percentile of option prices.

The historical-contract rule
----------------------------
Today's option contract did not exist a year ago, so each historical day is
rebuilt independently: that day's then-current monthly expiry, that day's ATM
strike from that day's underlying, that day's actual CE and PE closing prices,
and the IV solved from those prices. The current observation uses today's
monthly expiry and ATM strike under the identical convention.

Data source
-----------
The only source is NSE via ``nse_client`` (nselib) - the same approved, configured
options source the rest of OmniSpread uses. There is no Yahoo, Sensibull, browser,
or secondary-source path, and no hardcoded fallback. If the source is unavailable,
rate-limited, incomplete, or a contract cannot be resolved, the ticker's IVP is
``Unavailable`` (represented as ``None`` here) - never a substituted metric.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timedelta

import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm

from derivatives_backtest import RISK_FREE_RATE, _clean, instrument_types, nse_symbol
from nse_client import fetch_option

logger = logging.getLogger("OmniSpread.ivp")

# Sentinel a caller shows verbatim when a ticker's IVP cannot be computed.
UNAVAILABLE = "Unavailable"

# --- IVP window and sample rules -------------------------------------------
# Prior observations considered when ranking the current ATM IV.
IVP_LOOKBACK_TRADING_DAYS = 250
# Below this many valid prior observations the percentile is not trustworthy,
# so the ticker reports Unavailable rather than a percentile off a thin sample.
MIN_VALID_OBSERVATIONS = 60

# --- VRP (variance risk premium) -------------------------------------------
# VRP = current ATM IV - realised volatility, in annualised volatility points.
# Positive means options are priced above what the stock has actually delivered
# (vol is rich -> selling premium, e.g. credit spreads, is favoured); negative
# means options are cheap (buying them, e.g. futures + long option, is favoured).
# Realised vol is measured over one monthly option cycle to match the tenor of
# the contracts a pair trade actually uses. VRP is display-only, like IVP.
VRP_REALIZED_WINDOW = 21
# Calendar days of history to request. A year of trading days is ~250; the extra
# margin absorbs the observations discarded for illiquid or near-expiry days.
FETCH_CALENDAR_DAYS = 400

# --- Monthly-expiry / contract rules ---------------------------------------
# "Monthly expiry" is the last expiry listed within a calendar month. Weekly
# expiries (indices only) are every earlier expiry in the same month and are
# excluded. See _monthly_expiries.
#
# An option within MIN_DTE_CALENDAR_DAYS of expiry has unstable IV (tiny time
# value, jumpy quotes), so such a front contract is rolled to the next monthly
# and any day with no monthly at least this far out is discarded.
MIN_DTE_CALENDAR_DAYS = 7

# --- IV solver bounds ------------------------------------------------------
# Black-Scholes is inverted with scipy's Brent method (an established, bracketed
# root finder). A solved IV outside this band is treated as a bad observation.
IV_SOLVER_LOW = 1e-4
IV_SOLVER_HIGH = 5.0
IV_PLAUSIBLE_MIN = 0.01   # 1% annualised
IV_PLAUSIBLE_MAX = 3.0    # 300% annualised

# --- Cache -----------------------------------------------------------------
# Compact, reproducible per-ticker observation series, keyed by ticker and the
# as-of date, so a given day is downloaded at most once per ticker. Kept outside
# tracked source files (see .gitignore) and pruned after CACHE_RETENTION_DAYS.
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ivp_cache")
CACHE_RETENTION_DAYS = 7
SOURCE_NAME = "nselib/NSE"


# ---------------------------------------------------------------------------
#   Black-Scholes implied volatility (scipy Brent inversion)
# ---------------------------------------------------------------------------
def _bs_price(option_type: str, spot: float, strike: float, years: float,
              rate: float, sigma: float) -> float:
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years) / (sigma * math.sqrt(years))
    d2 = d1 - sigma * math.sqrt(years)
    if option_type == "CE":
        return spot * norm.cdf(d1) - strike * math.exp(-rate * years) * norm.cdf(d2)
    return strike * math.exp(-rate * years) * norm.cdf(-d2) - spot * norm.cdf(-d1)


def implied_vol(option_type: str, spot: float, strike: float, years: float,
                price: float, rate: float = RISK_FREE_RATE) -> float | None:
    """Solve Black-Scholes for volatility, or return None for a bad quote.

    Rejects prices that are non-positive, below intrinsic value, or otherwise
    economically impossible, and any solve landing outside the plausibility band.
    """
    if spot <= 0 or strike <= 0 or years <= 0 or price <= 0:
        return None
    intrinsic = max(spot - strike, 0.0) if option_type == "CE" else max(strike - spot, 0.0)
    if price <= intrinsic:
        return None  # no time value -> no volatility to solve for
    # Theoretical ceiling: a call cannot exceed spot; a put cannot exceed the
    # discounted strike.
    upper_bound = spot if option_type == "CE" else strike * math.exp(-rate * years)
    if price >= upper_bound:
        return None

    def objective(sigma: float) -> float:
        return _bs_price(option_type, spot, strike, years, rate, sigma) - price

    try:
        if objective(IV_SOLVER_LOW) > 0 or objective(IV_SOLVER_HIGH) < 0:
            return None  # price not bracketed by the solver range
        sigma = brentq(objective, IV_SOLVER_LOW, IV_SOLVER_HIGH, xtol=1e-6, maxiter=100)
    except (ValueError, RuntimeError):
        return None
    if not (IV_PLAUSIBLE_MIN <= sigma <= IV_PLAUSIBLE_MAX):
        return None
    return float(sigma)


# ---------------------------------------------------------------------------
#   Monthly-expiry selection
# ---------------------------------------------------------------------------
def _monthly_expiries(expiries) -> list[pd.Timestamp]:
    """The last expiry within each calendar month (monthly contracts only)."""
    by_month: dict[tuple[int, int], pd.Timestamp] = {}
    for expiry in sorted(pd.Timestamp(e) for e in expiries):
        key = (expiry.year, expiry.month)
        if key not in by_month or expiry > by_month[key]:
            by_month[key] = expiry
    return sorted(by_month.values())


def _front_monthly(monthlies: list[pd.Timestamp], day: pd.Timestamp) -> pd.Timestamp | None:
    """The nearest monthly expiry at least MIN_DTE_CALENDAR_DAYS beyond `day`."""
    eligible = [e for e in monthlies if (e - day).days >= MIN_DTE_CALENDAR_DAYS]
    return eligible[0] if eligible else None


# ---------------------------------------------------------------------------
#   Observation reconstruction
# ---------------------------------------------------------------------------
def _atm_observation(day_chain: pd.DataFrame, expiry: pd.Timestamp,
                     day: pd.Timestamp) -> dict | None:
    """Build one day's ATM-IV observation, or None if the day is unusable.

    Requires a valid, traded CE and PE at the ATM strike; the day is kept only
    when both sides solve. The two IVs are averaged into the daily observation.
    """
    chain = day_chain[day_chain["expiry"] == expiry]
    spot = pd.to_numeric(chain["UNDERLYING_VALUE"], errors="coerce").dropna()
    if spot.empty or spot.iloc[0] <= 0:
        return None
    spot = float(spot.iloc[0])

    strikes = sorted(float(s) for s in chain["STRIKE_PRICE"].dropna().unique())
    if not strikes:
        return None
    atm = min(strikes, key=lambda s: abs(s - spot))
    years = max((expiry - day).days, 1) / 365.0

    legs = {}
    for option_type in ("CE", "PE"):
        row = chain[(chain["STRIKE_PRICE"] == atm) & (chain["OPTION_TYPE"] == option_type)]
        if row.empty:
            return None
        row = row.iloc[0]
        volume = pd.to_numeric(row.get("TOT_TRADED_QTY"), errors="coerce")
        if pd.isna(volume) or volume <= 0:
            return None  # untraded strike carries a stale settlement mark
        price = float(row["CLOSING_PRICE"])
        iv = implied_vol(option_type, spot, atm, years, price)
        if iv is None:
            return None
        legs[option_type] = (price, iv)

    ce_price, ce_iv = legs["CE"]
    pe_price, pe_iv = legs["PE"]
    return {
        "date": day.strftime("%Y-%m-%d"),
        "expiry": expiry.strftime("%Y-%m-%d"),
        "atm_strike": atm,
        "ce_price": round(ce_price, 4),
        "pe_price": round(pe_price, 4),
        "ce_iv": round(ce_iv, 6),
        "pe_iv": round(pe_iv, 6),
        "atm_iv": round((ce_iv + pe_iv) / 2.0, 6),
    }


def _build_series(ticker: str) -> list[dict]:
    """Reconstruct the per-day ATM-IV observation series for one ticker.

    One nselib call covers the whole window (every date, strike and expiry),
    including the most recent trading day, which supplies the current
    observation. Raises on a data-source failure so the caller can report
    Unavailable rather than an empty (falsely valid) series.
    """
    end = datetime.now()
    start = end - timedelta(days=FETCH_CALENDAR_DAYS)
    symbol = nse_symbol(ticker)
    _, option_instrument = instrument_types(ticker)

    raw = fetch_option(
        symbol=symbol, instrument=option_instrument, option_type=None,
        from_date=start.strftime("%d-%m-%Y"), to_date=end.strftime("%d-%m-%Y"),
    )
    chain = _clean(raw)
    if chain.empty:
        return []

    monthlies = _monthly_expiries(chain["expiry"].dropna().unique())
    observations: list[dict] = []
    for day, day_chain in chain.groupby("date"):
        day = pd.Timestamp(day)
        expiry = _front_monthly(monthlies, day)
        if expiry is None:
            continue
        obs = _atm_observation(day_chain, expiry, day)
        if obs is not None:
            observations.append(obs)

    observations.sort(key=lambda o: o["date"])
    return observations


# ---------------------------------------------------------------------------
#   Cache
# ---------------------------------------------------------------------------
def _cache_path(ticker: str, as_of: str) -> str:
    safe = ticker.replace("/", "_").replace("^", "_")
    return os.path.join(CACHE_DIR, f"{safe}_{as_of}.json")


def _prune_cache() -> None:
    if not os.path.isdir(CACHE_DIR):
        return
    cutoff = time.time() - CACHE_RETENTION_DAYS * 86400
    for name in os.listdir(CACHE_DIR):
        path = os.path.join(CACHE_DIR, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def _load_or_build_series(ticker: str) -> list[dict]:
    as_of = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(CACHE_DIR, exist_ok=True)
    _prune_cache()
    path = _cache_path(ticker, as_of)

    if os.path.exists(path):
        try:
            with open(path) as fh:
                return json.load(fh)["observations"]
        except (OSError, ValueError, KeyError):
            pass  # corrupt cache -> rebuild

    series = _build_series(ticker)
    payload = {
        "ticker": ticker,
        "as_of": as_of,
        "source": SOURCE_NAME,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "observations": series,
    }
    try:
        with open(path, "w") as fh:
            json.dump(payload, fh)
    except OSError:
        pass  # cache is best-effort; the value is still returned
    return series


# ---------------------------------------------------------------------------
#   Public API
# ---------------------------------------------------------------------------
def _percentile(series: list[dict]) -> float | None:
    """IV percentile of the latest observation against its priors, or None."""
    if len(series) < MIN_VALID_OBSERVATIONS + 1:
        return None  # need the priors plus the current observation
    window = series[-(IVP_LOOKBACK_TRADING_DAYS + 1):]
    current = window[-1]["atm_iv"]
    priors = [obs["atm_iv"] for obs in window[:-1]]
    if len(priors) < MIN_VALID_OBSERVATIONS:
        return None
    below = sum(1 for iv in priors if iv < current)
    return round(100.0 * below / len(priors), 1)


def compute_atm_ivp(ticker: str) -> float | None:
    """ATM IV percentile for one ticker, or None (Unavailable).

    Never raises: any data-source or computation failure yields None so the scan
    is unaffected.
    """
    try:
        series = _load_or_build_series(ticker)
    except Exception as exc:  # noqa: BLE001 - display-only; must never break a scan
        logger.warning("IVP unavailable for %s: %s", ticker, str(exc)[:80])
        return None
    return _percentile(series)


def compute_metrics(ticker: str) -> dict[str, float | None]:
    """Both display metrics for one ticker: IV percentile and current ATM IV.

    ``ivp`` is the percentile (None if the sample is too thin); ``current_iv`` is
    the latest valid ATM IV, used by the caller to form VRP against realised vol.
    Never raises.
    """
    try:
        series = _load_or_build_series(ticker)
    except Exception as exc:  # noqa: BLE001 - display-only; must never break a scan
        logger.warning("IVP/VRP unavailable for %s: %s", ticker, str(exc)[:80])
        return {"ivp": None, "current_iv": None}
    current_iv = series[-1]["atm_iv"] if series else None
    return {"ivp": _percentile(series), "current_iv": current_iv}


def compute_ivp_map(tickers) -> dict[str, float | None]:
    """{ticker: percentile-or-None} for a set of tickers, each independent."""
    return {ticker: compute_atm_ivp(ticker) for ticker in dict.fromkeys(tickers)}


def compute_metrics_map(tickers) -> dict[str, dict[str, float | None]]:
    """{ticker: {"ivp":..., "current_iv":...}} — series built once per ticker."""
    return {ticker: compute_metrics(ticker) for ticker in dict.fromkeys(tickers)}


def format_ivp(value: float | None) -> str:
    """Render a computed IVP for display, or the Unavailable sentinel."""
    return UNAVAILABLE if value is None else f"{value:.1f}%"


def format_vrp(iv: float | None, realized_vol: float | None) -> str:
    """VRP in signed volatility points, or Unavailable if either input is missing.

    e.g. current IV 24.0% and realised 20.0% -> "+4.0". Both inputs are annualised
    volatility fractions.
    """
    if iv is None or realized_vol is None:
        return UNAVAILABLE
    return f"{(iv - realized_vol) * 100.0:+.1f}"
