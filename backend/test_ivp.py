"""Tests for the display-only ATM IV Percentile feature.

None of these tests touch the network: the single NSE entry point
(ivp.fetch_option) is monkeypatched. They prove the eligibility gate, the
historical-contract rule, the IVP arithmetic, the Unavailable behaviour, that no
secondary data source is reachable, and that IVP never perturbs a scan.
"""
import json
import os
import time

import pandas as pd
import pytest

import ivp
from engine import OmniSpreadEngine


# ---------------------------------------------------------------------------
#   Synthetic NSE option chain (raw, pre-_clean) built from Black-Scholes
# ---------------------------------------------------------------------------
def _raw_chain(days, expiries, spot, sigma, lot=100, volume=500):
    """Build a raw option frame priced at `sigma` so IV recovers to `sigma`.

    `days` and `expiries` are pandas Timestamps. Each day lists a ladder of
    strikes for every expiry, with CE/PE priced by Black-Scholes.
    """
    rows = []
    for day in days:
        for expiry in expiries:
            years = max((expiry - day).days, 1) / 365.0
            for strike in range(int(spot * 0.8), int(spot * 1.2) + 1, 20):
                for opt in ("CE", "PE"):
                    price = ivp._bs_price(opt, spot, strike, years, ivp.RISK_FREE_RATE, sigma)
                    rows.append({
                        "TIMESTAMP": day.strftime("%d-%b-%Y"),
                        "EXPIRY_DT": expiry.strftime("%d-%b-%Y"),
                        "STRIKE_PRICE": strike,
                        "OPTION_TYPE": opt,
                        "CLOSING_PRICE": round(max(price, 0.05), 2),
                        "UNDERLYING_VALUE": spot,
                        "TOT_TRADED_QTY": volume,
                        "MARKET_LOT": lot,
                    })
    return pd.DataFrame(rows)


# ===========================================================================
#   1 & 2 — eligibility gate
# ===========================================================================
def _engine(period, interval, start=None, end=None):
    return OmniSpreadEngine(
        tickers=["INFY.NS", "TCS.NS"], period=period, interval=interval,
        start_date=start, end_date=end,
    )


def _results():
    return [{"pair": "INFY/TCS", "x": "INFY.NS", "y": "TCS.NS", "prob_profit": 90.0}]


def _metrics(mapping):
    """Adapt a {ticker: (ivp, current_iv)} dict to compute_metrics_map's shape."""
    return lambda tickers: {t: {"ivp": iv, "current_iv": cur} for t, (iv, cur) in mapping.items()}


def test_ivp_and_vrp_attached_only_for_1y_1d(monkeypatch):
    monkeypatch.setattr(ivp, "compute_metrics_map",
                        _metrics({"INFY.NS": (72.0, 0.24), "TCS.NS": (None, None)}))
    engine = _engine("1y", "1d")
    monkeypatch.setattr(engine, "_realized_vol", lambda t, w: 0.20 if t == "INFY.NS" else None)
    results = _results()
    engine._attach_vol_metrics(results)
    assert results[0]["x_atm_ivp_250d"] == "72.0%"
    assert results[0]["x_atm_vrp_21d"] == "+4.0"          # 24% IV - 20% RV
    assert results[0]["y_atm_ivp_250d"] == "Unavailable"
    assert results[0]["y_atm_vrp_21d"] == "Unavailable"   # no current IV


@pytest.mark.parametrize("period,interval,start,end", [
    ("3y", "1d", None, None),
    ("60d", "15m", None, None),
    ("1y", "15m", None, None),      # right period, wrong interval
    ("6mo", "1d", None, None),
    ("1y", "1d", "2024-01-01", "2025-01-01"),   # 1y/1d strings but a custom range
])
def test_metrics_absent_and_never_invoked_for_non_eligible(monkeypatch, period, interval, start, end):
    called = []
    monkeypatch.setattr(ivp, "compute_metrics_map", lambda tickers: called.append(tickers) or {})
    results = _results()
    _engine(period, interval, start, end)._attach_vol_metrics(results)
    assert called == []                                   # never invoked
    for key in ("x_atm_ivp_250d", "y_atm_ivp_250d", "x_atm_vrp_21d", "y_atm_vrp_21d"):
        assert key not in results[0]                      # every column absent


def test_metrics_failure_never_breaks_the_scan(monkeypatch):
    def boom(tickers):
        raise RuntimeError("NSE down")
    monkeypatch.setattr(ivp, "compute_metrics_map", boom)
    results = _results()
    _engine("1y", "1d")._attach_vol_metrics(results)      # must not raise
    assert "x_atm_ivp_250d" not in results[0]
    assert "x_atm_vrp_21d" not in results[0]


# ===========================================================================
#   3 — historical observations use each day's then-current monthly expiry
# ===========================================================================
def test_history_uses_each_days_own_monthly_not_todays_contract(monkeypatch):
    spot = 1000.0
    # Three monthly expiries; a day in June must use the June monthly, a day in
    # July the July monthly - never the latest (August) contract for all days.
    expiries = [pd.Timestamp("2026-06-25"), pd.Timestamp("2026-07-30"), pd.Timestamp("2026-08-27")]
    days = [pd.Timestamp("2026-06-02"), pd.Timestamp("2026-07-02"), pd.Timestamp("2026-08-03")]
    chain = _raw_chain(days, expiries, spot, sigma=0.25)
    monkeypatch.setattr(ivp, "fetch_option", lambda **kw: chain)

    series = ivp._build_series("INFY.NS")
    picked = {obs["date"]: obs["expiry"] for obs in series}
    assert picked["2026-06-02"] == "2026-06-25"
    assert picked["2026-07-02"] == "2026-07-30"
    assert picked["2026-08-03"] == "2026-08-27"


def test_monthly_expiry_ignores_weeklies():
    expiries = ["2026-06-04", "2026-06-11", "2026-06-18", "2026-06-25", "2026-07-30"]
    monthlies = [str(e.date()) for e in ivp._monthly_expiries(expiries)]
    assert monthlies == ["2026-06-25", "2026-07-30"]       # last of each month only


def test_near_expiry_rolls_to_next_monthly():
    monthlies = ivp._monthly_expiries(["2026-06-25", "2026-07-30"])
    # 6 days before the June monthly (< MIN_DTE_CALENDAR_DAYS) -> roll to July.
    assert ivp._front_monthly(monthlies, pd.Timestamp("2026-06-20")).date() == pd.Timestamp("2026-07-30").date()


# ===========================================================================
#   4 — ATM selection and CE/PE combination
# ===========================================================================
def test_atm_observation_averages_ce_and_pe_iv():
    spot = 1000.0
    expiry = pd.Timestamp("2026-07-30")
    day = pd.Timestamp("2026-07-01")
    chain = ivp._clean(_raw_chain([day], [expiry], spot, sigma=0.25))
    obs = ivp._atm_observation(chain, expiry, day)
    assert obs["atm_strike"] == 1000            # nearest strike to spot
    assert obs["ce_iv"] == pytest.approx(0.25, abs=2e-3)
    assert obs["pe_iv"] == pytest.approx(0.25, abs=2e-3)
    assert obs["atm_iv"] == pytest.approx(0.25, abs=2e-3)


# ===========================================================================
#   5 — IVP arithmetic, ties, minimum sample
# ===========================================================================
def _fake_series(values):
    return [{"atm_iv": v} for v in values]


def test_ivp_arithmetic_and_ties(monkeypatch):
    # 60 priors, then current. Priors below current count toward the percentile.
    priors = [0.10] * 30 + [0.30] * 30
    monkeypatch.setattr(ivp, "_load_or_build_series", lambda t: _fake_series(priors + [0.20]))
    assert ivp.compute_atm_ivp("X") == 50.0    # 30 of 60 below 0.20

    # Ties are strictly "<", so equal priors do not count.
    monkeypatch.setattr(ivp, "_load_or_build_series", lambda t: _fake_series([0.20] * 61 + [0.20]))
    assert ivp.compute_atm_ivp("X") == 0.0


def test_ivp_requires_minimum_sample(monkeypatch):
    monkeypatch.setattr(ivp, "_load_or_build_series",
                        lambda t: _fake_series([0.2] * (ivp.MIN_VALID_OBSERVATIONS)))  # one short of min+current
    assert ivp.compute_atm_ivp("X") is None

    monkeypatch.setattr(ivp, "_load_or_build_series",
                        lambda t: _fake_series([0.2] * ivp.MIN_VALID_OBSERVATIONS + [0.3]))
    assert ivp.compute_atm_ivp("X") == 100.0   # exactly min priors + current


# ===========================================================================
#   6 — invalid / stale / illiquid data returns Unavailable, never fake
# ===========================================================================
def test_untraded_strike_is_discarded():
    spot = 1000.0
    expiry = pd.Timestamp("2026-07-30")
    day = pd.Timestamp("2026-07-01")
    chain = ivp._clean(_raw_chain([day], [expiry], spot, sigma=0.25, volume=0))  # nothing traded
    assert ivp._atm_observation(chain, expiry, day) is None


def test_below_intrinsic_and_nonpositive_prices_yield_no_iv():
    assert ivp.implied_vol("CE", 1000, 900, 0.1, 5.0) is None     # price below 100 intrinsic
    assert ivp.implied_vol("PE", 1000, 1000, 0.1, 0.0) is None    # non-positive
    assert ivp.implied_vol("CE", 1000, 1000, 0.1, 999.0) is None  # above ceiling


def test_source_unavailable_returns_none_not_fake(monkeypatch, tmp_path):
    from nse_client import NseUnavailable
    monkeypatch.setattr(ivp, "CACHE_DIR", str(tmp_path))          # never hit a real cached file
    def blocked(**kw):
        raise NseUnavailable("NSE blocked")
    monkeypatch.setattr(ivp, "fetch_option", blocked)
    assert ivp.compute_atm_ivp("INFY.NS") is None                 # Unavailable, no substitute


# ===========================================================================
#   7 — no Yahoo / Sensibull / hidden secondary source
# ===========================================================================
def test_no_secondary_data_source_imported():
    """Inspect real imports (not prose): the only data path is nse_client."""
    import ast
    tree = ast.parse(open(ivp.__file__).read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for banned in ("yfinance", "yahoo", "sensibull", "urllib", "selenium", "requests", "curl_cffi"):
        assert banned not in imported, f"ivp.py must not import {banned}"
    assert "nse_client" in imported          # the one approved source


def test_only_nse_client_is_the_data_path(monkeypatch, tmp_path):
    """If the one NSE entry point is severed, IVP is Unavailable - no fallback."""
    monkeypatch.setattr(ivp, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(ivp, "fetch_option",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("severed")))
    assert ivp.compute_atm_ivp("INFY.NS") is None


# ===========================================================================
#   8 — IVP does not alter existing results / ranking / metrics
# ===========================================================================
def test_vol_metrics_only_add_keys_and_preserve_order_and_values(monkeypatch):
    monkeypatch.setattr(ivp, "compute_metrics_map",
                        _metrics({"A.NS": (10.0, 0.3), "B.NS": (90.0, 0.2), "C.NS": (50.0, 0.25)}))
    results = [
        {"pair": "A/B", "x": "A.NS", "y": "B.NS", "prob_profit": 95.0, "half_life": 8, "z_score": 2.1},
        {"pair": "A/C", "x": "A.NS", "y": "C.NS", "prob_profit": 60.0, "half_life": 12, "z_score": -2.4},
    ]
    before = [dict(r) for r in results]
    engine = _engine("1y", "1d")
    monkeypatch.setattr(engine, "_realized_vol", lambda t, w: 0.22)
    engine._attach_vol_metrics(results)

    assert [r["pair"] for r in results] == [r["pair"] for r in before]      # order preserved
    for r, b in zip(results, before):
        for key, value in b.items():
            assert r[key] == value                                         # every prior field intact
        assert set(r) - set(b) == {
            "x_atm_ivp_250d", "y_atm_ivp_250d", "x_atm_vrp_21d", "y_atm_vrp_21d",
        }


def test_vrp_formatting_signs_and_unavailable():
    assert ivp.format_vrp(0.24, 0.20) == "+4.0"     # rich vol
    assert ivp.format_vrp(0.18, 0.22) == "-4.0"     # cheap vol
    assert ivp.format_vrp(0.20, 0.20) == "+0.0"
    assert ivp.format_vrp(None, 0.20) == "Unavailable"
    assert ivp.format_vrp(0.24, None) == "Unavailable"


def test_realized_vol_uses_scan_prices_without_refetch():
    import numpy as np
    eng = _engine("1y", "1d")
    # 40 flat-drift closes with known noise; realised vol must be finite and positive.
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    closes = pd.Series(1000 * np.exp(np.cumsum(np.random.default_rng(0).normal(0, 0.01, 40))), index=idx)
    eng.data = pd.DataFrame({"INFY.NS": closes})
    rv = eng._realized_vol("INFY.NS", 21)
    assert rv is not None and 0 < rv < 2
    assert eng._realized_vol("MISSING.NS", 21) is None       # ticker not in data
    assert eng._realized_vol("INFY.NS", 500) is None         # window longer than data


def test_current_iv_available_even_when_ivp_sample_thin(monkeypatch):
    """VRP should work off the latest IV even if there are too few priors for IVP."""
    monkeypatch.setattr(ivp, "_load_or_build_series",
                        lambda t: [{"atm_iv": 0.2}] * 5 + [{"atm_iv": 0.3}])
    m = ivp.compute_metrics("X")
    assert m["ivp"] is None            # thin sample -> no percentile
    assert m["current_iv"] == 0.3      # but current IV is still available for VRP


# ===========================================================================
#   9 — cache is bounded and untracked
# ===========================================================================
def test_cache_is_gitignored():
    gitignore = open(os.path.join(os.path.dirname(ivp.__file__), "..", ".gitignore")).read()
    assert ".ivp_cache" in gitignore


def test_prune_removes_stale_cache_files(tmp_path, monkeypatch):
    monkeypatch.setattr(ivp, "CACHE_DIR", str(tmp_path))
    fresh = tmp_path / "INFY.NS_2026-07-19.json"
    stale = tmp_path / "OLD.NS_2020-01-01.json"
    fresh.write_text("{}")
    stale.write_text("{}")
    old = time.time() - (ivp.CACHE_RETENTION_DAYS + 2) * 86400
    os.utime(stale, (old, old))
    ivp._prune_cache()
    assert fresh.exists() and not stale.exists()


def test_cache_records_are_compact_and_reproducible(monkeypatch, tmp_path):
    monkeypatch.setattr(ivp, "CACHE_DIR", str(tmp_path))
    spot = 1000.0
    expiry = pd.Timestamp("2026-07-30")
    day = pd.Timestamp("2026-07-01")
    monkeypatch.setattr(ivp, "fetch_option", lambda **kw: _raw_chain([day], [expiry], spot, 0.25))
    ivp._load_or_build_series("INFY.NS")
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text())
    assert payload["source"] == ivp.SOURCE_NAME and "timestamp" in payload
    obs = payload["observations"][0]
    assert set(obs) == {"date", "expiry", "atm_strike", "ce_price", "pe_price",
                        "ce_iv", "pe_iv", "atm_iv"}
