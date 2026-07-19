from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd
from margin_estimator import estimate_margin

logger = logging.getLogger("OmniSpread.derivatives")

FetchFuture = Callable[..., pd.DataFrame]
FetchOption = Callable[..., pd.DataFrame]

# Credit-spread strike selection rules.
#   legacy - sold strike a flat 2% OTM, hedge three listed strikes further out.
#   vol    - strikes placed in units of the expected move implied by the ATM
#            straddle, so risk is scaled to volatility and time rather than to
#            whatever strike spacing the exchange happens to list.
STRIKE_RULE_LEGACY = "legacy"
STRIKE_RULE_VOL = "vol"
DEFAULT_SOLD_SD = 1.0
DEFAULT_HEDGE_SD = 2.5

# An ATM straddle is worth about S*sigma*sqrt(T)*sqrt(2/pi) - the mean absolute
# move - so the one-standard-deviation move is straddle / sqrt(2/pi).
STRADDLE_TO_SD = 1.0 / math.sqrt(2.0 / math.pi)
RISK_FREE_RATE = 0.065


@dataclass(frozen=True)
class Contract:
    symbol: str
    expiry: pd.Timestamp
    strike: float
    option_type: str
    lot_size: int


def nse_symbol(ticker: str) -> str:
    aliases = {
        "^NSEI": "NIFTY",
        "^NSEBANK": "BANKNIFTY",
        "NIFTY_FIN_SERVICE": "FINNIFTY",
        "NIFTY_FIN_SERVICE.NS": "FINNIFTY",
    }
    return aliases.get(ticker, ticker.removesuffix(".NS").removesuffix(".BO"))


def instrument_types(ticker: str) -> tuple[str, str]:
    symbol = nse_symbol(ticker)
    if symbol in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}:
        return "FUTIDX", "OPTIDX"
    return "FUTSTK", "OPTSTK"


def whole_lot_hedge(
    qty: float,
    x_lot: int,
    y_lot: int,
    max_lots: int = 8,
    tolerance: float = 0.05,
) -> tuple[int, int]:
    """Return a compact whole-lot approximation to qty X shares per Y share.

    Near-equal lot ratios are deliberately rounded to 1:1. Otherwise, prefer
    the smallest position whose share-ratio error is within five percent,
    rather than overfitting the statistical hedge ratio with many lots.
    """
    if qty <= 0 or x_lot <= 0 or y_lot <= 0:
        raise ValueError("Hedge ratio and market lots must be positive.")
    target_lot_ratio = qty * y_lot / x_lot
    if abs(target_lot_ratio - 1.0) / target_lot_ratio <= 0.12:
        return 1, 1

    candidates = [
        (
            abs((x_count / y_count) - target_lot_ratio) / target_lot_ratio,
            x_count + y_count,
            max(x_count, y_count),
            x_count,
            y_count,
        )
        for x_count in range(1, max_lots + 1)
        for y_count in range(1, max_lots + 1)
    ]
    acceptable = [candidate for candidate in candidates if candidate[0] <= tolerance]
    if acceptable:
        _, _, _, x_count, y_count = min(
            acceptable, key=lambda candidate: (candidate[1], candidate[0], candidate[2])
        )
        return x_count, y_count

    _, _, _, x_count, y_count = min(
        candidates, key=lambda candidate: (candidate[0], candidate[1], candidate[2])
    )
    return x_count, y_count


def _dates(start: datetime, end: datetime) -> tuple[str, str]:
    return start.strftime("%d-%m-%Y"), end.strftime("%d-%m-%Y")


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    result = df.copy()
    result["date"] = pd.to_datetime(result["TIMESTAMP"], format="%d-%b-%Y")
    result["expiry"] = pd.to_datetime(result["EXPIRY_DT"], format="%d-%b-%Y")
    for column in ("CLOSING_PRICE", "STRIKE_PRICE", "MARKET_LOT", "UNDERLYING_VALUE",
                   "TOT_TRADED_QTY"):
        if column in result:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.sort_values("date")


def _nearest_expiry(df: pd.DataFrame, required_expiry: pd.Timestamp) -> pd.Timestamp:
    expiries = sorted(df.loc[df["expiry"] >= required_expiry, "expiry"].dropna().unique())
    if not expiries:
        raise ValueError(
            f"No contract expiry was available on or after {required_expiry.strftime('%d-%b-%Y')}."
        )
    return pd.Timestamp(expiries[0])


def _entry_rows(df: pd.DataFrame, expiry: pd.Timestamp) -> pd.DataFrame:
    rows = df[df["expiry"] == expiry]
    if rows.empty:
        return rows
    first_date = rows["date"].min()
    return rows[rows["date"] == first_date]


def _latest_rows(df: pd.DataFrame, expiry: pd.Timestamp) -> pd.DataFrame:
    rows = df[df["expiry"] == expiry]
    if rows.empty:
        return rows
    latest_date = rows["date"].max()
    return rows[rows["date"] == latest_date]


def _price_series(df: pd.DataFrame, contract: Contract) -> pd.Series:
    rows = df[
        (df["expiry"] == contract.expiry)
        & (df["OPTION_TYPE"] == contract.option_type)
        & (df["STRIKE_PRICE"] == contract.strike)
    ]
    series = rows.drop_duplicates("date", keep="last").set_index("date")["CLOSING_PRICE"]
    return series.sort_index().astype(float)


def _snapshot_price(df: pd.DataFrame, contract: Contract, snapshot: pd.DataFrame) -> float:
    rows = snapshot[
        (snapshot["expiry"] == contract.expiry)
        & (snapshot["OPTION_TYPE"] == contract.option_type)
        & (snapshot["STRIKE_PRICE"] == contract.strike)
    ]
    if rows.empty:
        raise ValueError(f"No price was available for {contract.symbol} {contract.strike} {contract.option_type}.")
    return float(rows.iloc[0]["CLOSING_PRICE"])


def _future_series(df: pd.DataFrame, expiry: pd.Timestamp) -> pd.Series:
    rows = df[df["expiry"] == expiry]
    return (
        rows.drop_duplicates("date", keep="last")
        .set_index("date")["CLOSING_PRICE"]
        .sort_index()
        .astype(float)
    )


def _choose_option(
    option_df: pd.DataFrame,
    expiry: pd.Timestamp,
    option_type: str,
    spot: float,
    offset: float,
    hedge_steps: int = 0,
    snapshot: pd.DataFrame | None = None,
) -> Contract:
    entry = snapshot if snapshot is not None else _entry_rows(option_df, expiry)
    entry = entry[(entry["OPTION_TYPE"] == option_type) & entry["STRIKE_PRICE"].notna()]
    strikes = sorted(float(value) for value in entry["STRIKE_PRICE"].unique())
    if not strikes:
        raise ValueError(f"No {option_type} strikes were available for {expiry.strftime('%d-%b-%Y')}.")

    target = spot * (1.0 + offset)
    sold_index = min(range(len(strikes)), key=lambda i: abs(strikes[i] - target))
    selected_index = sold_index + hedge_steps
    if selected_index < 0 or selected_index >= len(strikes):
        raise ValueError("The requested three-strike hedge is outside the available option chain.")
    strike = strikes[selected_index]
    row = entry[entry["STRIKE_PRICE"] == strike].iloc[0]
    return Contract(
        symbol=str(row["SYMBOL"]),
        expiry=expiry,
        strike=strike,
        option_type=option_type,
        lot_size=int(row["MARKET_LOT"]),
    )


def _atm_expected_move(entry: pd.DataFrame, spot: float, expiry: pd.Timestamp) -> float:
    """One-standard-deviation move to expiry, read off the ATM straddle.

    Using the straddle means no volatility, tenor or rate input is needed - the
    traded option prices already embed them.

    NSE publishes a settlement price for strikes that never traded, so a stale
    leg can inflate the straddle badly. When exactly one side of the ATM pair
    traded, the other is rebuilt from put-call parity instead of trusted.
    """
    rows = entry[entry["STRIKE_PRICE"].notna()]
    if rows.empty:
        raise ValueError("No option strikes were available to size the expected move.")
    strikes = sorted(float(value) for value in rows["STRIKE_PRICE"].unique())
    atm = min(strikes, key=lambda strike: abs(strike - spot))

    as_of = pd.Timestamp(rows["date"].min())
    years = max((expiry - as_of).days, 1) / 365.0
    # Put-call parity: C - P = S - K*exp(-rT)
    forward_gap = spot - atm * math.exp(-RISK_FREE_RATE * years)

    def leg(option_type: str) -> tuple[float | None, float]:
        match = rows[(rows["STRIKE_PRICE"] == atm) & (rows["OPTION_TYPE"] == option_type)]
        if match.empty:
            return None, 0.0
        row = match.iloc[0]
        volume = pd.to_numeric(row.get("TOT_TRADED_QTY"), errors="coerce")
        return float(row["CLOSING_PRICE"]), float(0.0 if pd.isna(volume) else volume)

    call, call_volume = leg("CE")
    put, put_volume = leg("PE")
    if call is None and put is None:
        raise ValueError(f"No ATM option was available at strike {atm:g} to size the expected move.")
    if call is None:
        call = put + forward_gap
    elif put is None:
        put = call - forward_gap
    elif call_volume <= 0 < put_volume:
        call = put + forward_gap
    elif put_volume <= 0 < call_volume:
        put = call - forward_gap

    straddle = float(call) + float(put)
    if straddle <= 0:
        raise ValueError("The ATM straddle priced at or below zero; cannot size the expected move.")
    return straddle * STRADDLE_TO_SD


def _choose_option_by_move(
    option_df: pd.DataFrame,
    expiry: pd.Timestamp,
    option_type: str,
    spot: float,
    sd_multiple: float,
    expected_move: float,
    snapshot: pd.DataFrame | None = None,
) -> Contract:
    """Pick the listed strike closest to `sd_multiple` expected moves OTM."""
    entry = snapshot if snapshot is not None else _entry_rows(option_df, expiry)
    entry = entry[(entry["OPTION_TYPE"] == option_type) & entry["STRIKE_PRICE"].notna()]
    strikes = sorted(float(value) for value in entry["STRIKE_PRICE"].unique())
    if not strikes:
        raise ValueError(f"No {option_type} strikes were available for {expiry.strftime('%d-%b-%Y')}.")

    away = -1.0 if option_type == "PE" else 1.0
    target = spot + away * sd_multiple * expected_move
    strike = min(strikes, key=lambda value: abs(value - target))
    row = entry[entry["STRIKE_PRICE"] == strike].iloc[0]
    return Contract(
        symbol=str(row["SYMBOL"]),
        expiry=expiry,
        strike=strike,
        option_type=option_type,
        lot_size=int(row["MARKET_LOT"]),
    )


def _step_strike(
    option_df: pd.DataFrame,
    expiry: pd.Timestamp,
    option_type: str,
    contract: Contract,
    snapshot: pd.DataFrame | None = None,
) -> Contract:
    """Return the next listed strike one step further OTM than `contract`."""
    entry = snapshot if snapshot is not None else _entry_rows(option_df, expiry)
    entry = entry[(entry["OPTION_TYPE"] == option_type) & entry["STRIKE_PRICE"].notna()]
    strikes = sorted(float(value) for value in entry["STRIKE_PRICE"].unique())
    index = strikes.index(contract.strike) + (-1 if option_type == "PE" else 1)
    if index < 0 or index >= len(strikes):
        raise ValueError(
            "The volatility-scaled hedge collapsed onto the sold strike and the "
            "option chain has no further strike to step to."
        )
    strike = strikes[index]
    row = entry[entry["STRIKE_PRICE"] == strike].iloc[0]
    return Contract(
        symbol=str(row["SYMBOL"]),
        expiry=expiry,
        strike=strike,
        option_type=option_type,
        lot_size=int(row["MARKET_LOT"]),
    )


def _entry_price(entry: pd.DataFrame, strike: float, option_type: str) -> float | None:
    row = entry[(entry["STRIKE_PRICE"] == strike) & (entry["OPTION_TYPE"] == option_type)]
    if row.empty:
        return None
    price = float(row.iloc[0]["CLOSING_PRICE"])
    return price if price > 0 else None


def _traded(entry: pd.DataFrame, strike: float, option_type: str) -> bool:
    """Whether the strike actually traded on the entry day.

    Untraded strikes carry a settlement mark rather than a real quote. Far OTM
    those marks are often far too rich, which makes a wide hedge look more
    expensive than the option it protects - enough to drive the net credit
    negative - so they must be excluded from the search.
    """
    row = entry[(entry["STRIKE_PRICE"] == strike) & (entry["OPTION_TYPE"] == option_type)]
    if row.empty:
        return False
    volume = pd.to_numeric(row.iloc[0].get("TOT_TRADED_QTY"), errors="coerce")
    return bool(volume and volume > 0)


def _optimise_hedge(
    entry: pd.DataFrame,
    expiry: pd.Timestamp,
    option_type: str,
    spot: float,
    sold: Contract,
    ceiling_strike: float,
    is_index: bool,
) -> Contract | None:
    """Hedge strike with the best credit per rupee of margin.

    Widening the wing collects more credit but raises the margin blocked against
    the position. Both move together, so there is a genuine optimum rather than
    a "wider is better" gradient; this walks the listed strikes and picks it.

    Margin comes from the real estimator, so the trade-off is measured against
    the same number the app reports. Lot size cancels from the ratio, so the
    search runs one lot deep.
    """
    sold_price = _entry_price(entry, sold.strike, option_type)
    if sold_price is None:
        return None

    away = -1.0 if option_type == "PE" else 1.0
    candidates = sorted(
        (float(value) for value in entry["STRIKE_PRICE"].unique()),
        key=lambda strike: abs(strike - sold.strike),
    )

    best: tuple[float, Contract] | None = None
    for strike in candidates:
        if away * (strike - sold.strike) <= 0:
            continue  # must sit further OTM than the sold strike
        if away * (strike - ceiling_strike) > 0:
            continue  # beyond the search ceiling
        if not _traded(entry, strike, option_type):
            continue
        hedge_price = _entry_price(entry, strike, option_type)
        if hedge_price is None:
            continue
        credit = sold_price - hedge_price
        if credit <= 0:
            continue

        row = entry[(entry["STRIKE_PRICE"] == strike) & (entry["OPTION_TYPE"] == option_type)].iloc[0]
        hedge = Contract(
            symbol=str(row["SYMBOL"]), expiry=expiry, strike=strike,
            option_type=option_type, lot_size=int(row["MARKET_LOT"]),
        )
        legs = [
            {"asset": "x", "symbol": sold.symbol, "instrument": option_type, "side": "SELL",
             "lots": 1, "lot_size": sold.lot_size, "strike": sold.strike, "spot": spot,
             "price": sold_price, "is_index": is_index},
            {"asset": "x", "symbol": hedge.symbol, "instrument": option_type, "side": "BUY",
             "lots": 1, "lot_size": hedge.lot_size, "strike": strike, "spot": spot,
             "price": hedge_price, "is_index": is_index},
        ]
        margin = estimate_margin(legs)["estimated_margin"]
        if margin <= 0:
            continue
        ratio = credit / margin
        if best is None or ratio > best[0]:
            best = (ratio, hedge)

    return best[1] if best else None


def _protective_contract(
    option_df: pd.DataFrame,
    expiry: pd.Timestamp,
    option_type: str,
    spot: float,
    *,
    strike_rule: str = STRIKE_RULE_VOL,
    sd: float = DEFAULT_SOLD_SD,
    snapshot: pd.DataFrame | None = None,
) -> Contract:
    """The long protective option bought against a futures leg.

    Placed the same distance out as a credit spread's sold strike, so the hedge
    scales with volatility and tenor instead of a flat percentage of spot.
    """
    if strike_rule == STRIKE_RULE_VOL:
        try:
            entry = snapshot if snapshot is not None else _entry_rows(option_df, expiry)
            move = _atm_expected_move(entry, spot, expiry)
            return _choose_option_by_move(
                option_df, expiry, option_type, spot, sd, move, snapshot=snapshot
            )
        except ValueError as exc:
            logger.warning(
                "Volatility-scaled protective strike unavailable for %s %s (%s); "
                "using the legacy rule.",
                option_type, expiry.strftime("%d-%b-%Y"), exc,
            )

    offset = -0.02 if option_type == "PE" else 0.02
    return _choose_option(option_df, expiry, option_type, spot, offset, snapshot=snapshot)


def _credit_spread_contracts(
    option_df: pd.DataFrame,
    expiry: pd.Timestamp,
    option_type: str,
    spot: float,
    *,
    strike_rule: str = STRIKE_RULE_VOL,
    sold_sd: float = DEFAULT_SOLD_SD,
    hedge_sd: float = DEFAULT_HEDGE_SD,
    snapshot: pd.DataFrame | None = None,
    is_index: bool = False,
) -> tuple[Contract, Contract]:
    """Return the (sold, hedge) contracts for one leg of a credit spread."""
    if strike_rule == STRIKE_RULE_VOL:
        try:
            entry = snapshot if snapshot is not None else _entry_rows(option_df, expiry)
            move = _atm_expected_move(entry, spot, expiry)
            sold = _choose_option_by_move(
                option_df, expiry, option_type, spot, sold_sd, move, snapshot=snapshot
            )

            # The hedge is chosen, not dictated: hedge_sd only bounds how far the
            # search may look, and the strike with the best credit-to-margin
            # ratio inside that range wins.
            away = -1.0 if option_type == "PE" else 1.0
            ceiling = spot + away * hedge_sd * move
            typed = entry[(entry["OPTION_TYPE"] == option_type) & entry["STRIKE_PRICE"].notna()]
            hedge = _optimise_hedge(
                typed, expiry, option_type, spot, sold, ceiling, is_index,
            )

            if hedge is None:
                # Nothing tradeable inside the range - fall back to placing the
                # hedge at the requested distance.
                hedge = _choose_option_by_move(
                    option_df, expiry, option_type, spot, hedge_sd, move, snapshot=snapshot
                )
            if hedge.strike == sold.strike:
                # A coarse ladder can collapse both legs onto one strike; step the
                # hedge one listed strike further OTM so the spread still has width.
                hedge = _step_strike(option_df, expiry, option_type, sold, snapshot=snapshot)
            return sold, hedge
        except ValueError as exc:
            # A shallow chain cannot always express a volatility-scaled spread
            # (1 SD can land at or beyond the outermost listed strike). Fall back
            # to the fixed-offset rule rather than failing the whole structure.
            logger.warning(
                "Volatility-scaled strikes unavailable for %s %s (%s); using the legacy rule.",
                option_type, expiry.strftime("%d-%b-%Y"), exc,
            )

    offset = -0.02 if option_type == "PE" else 0.02
    hedge_steps = -3 if option_type == "PE" else 3
    sold = _choose_option(option_df, expiry, option_type, spot, offset, snapshot=snapshot)
    hedge = _choose_option(
        option_df, expiry, option_type, spot, offset, hedge_steps, snapshot=snapshot
    )
    return sold, hedge


def _fetch_credit_snapshot(
    ticker: str,
    start: datetime,
    end: datetime,
    required_expiry: pd.Timestamp,
    fetch_future: FetchFuture,
    fetch_option: FetchOption,
) -> dict:
    symbol = nse_symbol(ticker)
    future_type, option_type = instrument_types(ticker)
    from_date, to_date = _dates(start, end)
    future_df = _clean(fetch_future(
        symbol=symbol, instrument=future_type, from_date=from_date, to_date=to_date
    ))
    if future_df.empty:
        raise ValueError(f"No current futures data was returned for {symbol}.")
    expiry = _nearest_expiry(future_df, required_expiry)
    future_rows = _latest_rows(future_df, expiry)
    if future_rows.empty:
        raise ValueError(f"No current futures contract was available for {symbol}.")

    option_df = _clean(fetch_option(
        symbol=symbol, instrument=option_type, option_type=None,
        from_date=from_date, to_date=to_date,
    ))
    option_rows = _latest_rows(option_df, expiry)
    if option_rows.empty:
        raise ValueError(f"No current option chain was available for {symbol} {expiry.strftime('%d-%b-%Y')}.")
    spot_values = future_rows["UNDERLYING_VALUE"].dropna()
    if spot_values.empty:
        spot_values = option_rows["UNDERLYING_VALUE"].dropna()
    if spot_values.empty:
        # NSElib occasionally leaves UNDERLYING_VALUE blank for otherwise valid
        # stock-derivative snapshots. The nearest-month futures close is the
        # best available proxy for selecting nearby strikes in that case.
        spot_values = future_rows["CLOSING_PRICE"].dropna()
    if spot_values.empty:
        raise ValueError(f"No underlying or futures reference price was available for {symbol}.")
    return {
        "symbol": symbol,
        "expiry": expiry,
        "as_of": pd.Timestamp(future_rows["date"].max()),
        "future_lot": int(future_rows.iloc[0]["MARKET_LOT"]),
        "spot": float(spot_values.iloc[0]),
        "options": option_df,
        "option_snapshot": option_rows,
        "is_index": future_type == "FUTIDX",
    }


def build_credit_spread_structure(
    *,
    x: str,
    y: str,
    qty: float,
    direction: str,
    fetch_future: FetchFuture,
    fetch_option: FetchOption,
    as_of_date: datetime | None = None,
    strike_rule: str = STRIKE_RULE_VOL,
    sold_sd: float = DEFAULT_SOLD_SD,
    hedge_sd: float = DEFAULT_HEDGE_SD,
) -> dict:
    as_of = as_of_date or datetime.now()
    start = as_of - timedelta(days=14)
    required_expiry = pd.Timestamp(as_of.date())
    assets = {
        "x": _fetch_credit_snapshot(x, start, as_of, required_expiry, fetch_future, fetch_option),
        "y": _fetch_credit_snapshot(y, start, as_of, required_expiry, fetch_future, fetch_option),
    }
    x_lots, y_lots = whole_lot_hedge(qty, assets["x"]["future_lot"], assets["y"]["future_lot"])
    lot_counts = {"x": x_lots, "y": y_lots}
    short_spread = direction in {"SHORT_SPREAD", "long_x_short_y"}
    signs = {"x": 1 if short_spread else -1, "y": -1 if short_spread else 1}
    legs: list[dict] = []

    for key, asset in assets.items():
        option_type = "PE" if signs[key] > 0 else "CE"
        sold, hedge = _credit_spread_contracts(
            asset["options"], asset["expiry"], option_type, asset["spot"],
            strike_rule=strike_rule, sold_sd=sold_sd, hedge_sd=hedge_sd,
            snapshot=asset["option_snapshot"], is_index=asset["is_index"],
        )
        count = lot_counts[key]
        for contract, side in ((sold, "SELL"), (hedge, "BUY")):
            legs.append({
                "asset": key,
                "symbol": asset["symbol"],
                "instrument": option_type,
                "side": side,
                "lots": count,
                "lot_size": contract.lot_size,
                "strike": contract.strike,
                "expiry": asset["expiry"].strftime("%d-%b-%Y"),
                "spot": round(asset["spot"], 2),
                "price": _snapshot_price(asset["options"], contract, asset["option_snapshot"]),
                "is_index": asset["is_index"],
            })

    actual_ratio = x_lots * assets["x"]["future_lot"] / (y_lots * assets["y"]["future_lot"])
    margin = estimate_margin(legs)
    return {
        "pair": f"{nse_symbol(x)}/{nse_symbol(y)}",
        "qty": qty,
        "direction": direction,
        "as_of": min(asset["as_of"] for asset in assets.values()).strftime("%d-%b-%Y"),
        "x_lots": x_lots,
        "y_lots": y_lots,
        "actual_ratio": round(actual_ratio, 4),
        "legs": legs,
        "margin": margin,
        "note": "Current structure only; prices and available strikes can change before execution.",
    }


def _fetch_symbol(
    ticker: str,
    start: datetime,
    fetch_end: datetime,
    required_expiry: pd.Timestamp,
    strategy: str,
    fetch_future: FetchFuture,
    fetch_option: FetchOption,
) -> dict:
    symbol = nse_symbol(ticker)
    future_type, option_type = instrument_types(ticker)
    from_date, to_date = _dates(start, fetch_end)
    future_df = _clean(fetch_future(
        symbol=symbol, instrument=future_type, from_date=from_date, to_date=to_date
    ))
    if future_df.empty:
        raise ValueError(f"No futures data was returned for {symbol}.")
    expiry = _nearest_expiry(future_df, required_expiry)
    future_entry = _entry_rows(future_df, expiry)
    if future_entry.empty:
        raise ValueError(f"No entry-date futures contract was available for {symbol}.")
    future_lot = int(future_entry.iloc[0]["MARKET_LOT"])
    spot = float(future_entry.iloc[0].get("UNDERLYING_VALUE", 0) or 0)

    result = {
        "symbol": symbol,
        "expiry": expiry,
        "future_lot": future_lot,
        "future": _future_series(future_df, expiry),
        "spot": spot,
        "is_index": future_type == "FUTIDX",
    }
    if strategy == "futures":
        return result

    option_df = _clean(fetch_option(
        symbol=symbol, instrument=option_type, option_type=None,
        from_date=from_date, to_date=to_date,
    ))
    if option_df.empty:
        raise ValueError(f"No options data was returned for {symbol}.")
    if not spot:
        option_entry = _entry_rows(option_df, expiry)
        spot = float(option_entry["UNDERLYING_VALUE"].dropna().iloc[0])
        result["spot"] = spot
    result["options"] = option_df
    return result


def run_derivatives_backtest(
    *,
    x: str,
    y: str,
    qty: float,
    direction: str,
    half_life: int,
    end_date: str,
    strategy: str,
    fetch_future: FetchFuture,
    fetch_option: FetchOption,
    strike_rule: str = STRIKE_RULE_VOL,
    sold_sd: float = DEFAULT_SOLD_SD,
    hedge_sd: float = DEFAULT_HEDGE_SD,
) -> dict:
    start = datetime.strptime(end_date, "%Y-%m-%d")
    # Always trade the nearest listed expiry, even when it falls before the
    # half-life exit - the trade then simply exits at expiry. Requiring the
    # contract to outlive the half-life pushed short trades onto contracts
    # several times their horizon, giving away most of the time decay.
    required_expiry = pd.Timestamp(start)
    # Reach past the nearest expiry so its final bar is included.
    fetch_end = max(
        start + timedelta(days=half_life * 3 + 14),
        start + timedelta(days=45),
    )

    assets = {
        "x": _fetch_symbol(x, start, fetch_end, required_expiry, strategy, fetch_future, fetch_option),
        "y": _fetch_symbol(y, start, fetch_end, required_expiry, strategy, fetch_future, fetch_option),
    }
    x_lots, y_lots = whole_lot_hedge(qty, assets["x"]["future_lot"], assets["y"]["future_lot"])
    lot_counts = {"x": x_lots, "y": y_lots}
    short_spread = direction in {"SHORT_SPREAD", "long_x_short_y"}
    signs = {"x": 1 if short_spread else -1, "y": -1 if short_spread else 1}

    leg_series: dict[str, pd.Series] = {}
    leg_meta: list[dict] = []
    credit_leg_prices: list[pd.Series] = []
    for key, asset in assets.items():
        sign = signs[key]
        count = lot_counts[key]
        symbol = asset["symbol"]
        expiry = asset["expiry"]
        future_lot = asset["future_lot"]

        if strategy in {"futures", "futures_options"}:
            future = asset["future"]
            name = f"{key}_future"
            leg_series[name] = sign * (future - future.iloc[0]) * future_lot * count
            leg_meta.append({
                "asset": key, "symbol": symbol, "instrument": "FUT",
                "side": "BUY" if sign > 0 else "SELL", "lots": count,
                "lot_size": future_lot, "expiry": expiry.strftime("%d-%b-%Y"),
                "spot": asset["spot"], "price": float(future.iloc[0]),
                "is_index": asset["is_index"],
            })

        if strategy == "futures_options":
            opt_type = "PE" if sign > 0 else "CE"
            contract = _protective_contract(
                asset["options"], expiry, opt_type, asset["spot"],
                strike_rule=strike_rule, sd=sold_sd,
            )
            option = _price_series(asset["options"], contract)
            name = f"{key}_{opt_type.lower()}"
            leg_series[name] = (option - option.iloc[0]) * contract.lot_size * count
            leg_meta.append({
                "asset": key, "symbol": symbol, "instrument": opt_type, "side": "BUY",
                "lots": count, "lot_size": contract.lot_size, "strike": contract.strike,
                "expiry": expiry.strftime("%d-%b-%Y"),
                "spot": asset["spot"], "price": float(option.iloc[0]),
                "is_index": asset["is_index"],
            })

        if strategy == "credit_spreads":
            opt_type = "PE" if sign > 0 else "CE"
            sold, hedge = _credit_spread_contracts(
                asset["options"], expiry, opt_type, asset["spot"],
                strike_rule=strike_rule, sold_sd=sold_sd, hedge_sd=hedge_sd,
                is_index=asset["is_index"],
            )
            sold_prices = _price_series(asset["options"], sold)
            hedge_prices = _price_series(asset["options"], hedge)
            leg_series[f"{key}_{opt_type.lower()}_short"] = -(sold_prices - sold_prices.iloc[0]) * sold.lot_size * count
            leg_series[f"{key}_{opt_type.lower()}_hedge"] = (hedge_prices - hedge_prices.iloc[0]) * hedge.lot_size * count
            for contract, side in ((sold, "SELL"), (hedge, "BUY")):
                contract_prices = sold_prices if side == "SELL" else hedge_prices
                leg_meta.append({
                    "asset": key, "symbol": symbol, "instrument": opt_type, "side": side,
                    "lots": count, "lot_size": contract.lot_size, "strike": contract.strike,
                    "expiry": expiry.strftime("%d-%b-%Y"),
                    "spot": asset["spot"],
                    "price": float(contract_prices.iloc[0]),
                    "is_index": asset["is_index"],
                })
                credit_leg_prices.append(contract_prices)

    pnl = pd.concat(leg_series, axis=1, join="inner").dropna()
    if pnl.empty:
        raise ValueError("The selected contracts had no overlapping daily closing prices.")
    common_expiry = min(asset["expiry"] for asset in assets.values())
    pnl = pnl[pnl.index <= common_expiry]
    if pnl.empty:
        raise ValueError("No common closing prices were available through contract expiry.")
    pnl["total"] = pnl.sum(axis=1)
    margin = estimate_margin(leg_meta)
    margin_base = margin["estimated_margin"]
    if margin_base <= 0:
        raise ValueError("Unable to estimate a positive entry margin for this structure.")
    pnl["pnl_pct"] = pnl["total"] * 100.0 / margin_base
    # Both exits are clamped to the data that exists. A trade entered recently
    # has neither reached its half-life nor its expiry, and reporting the last
    # available bar as either silently presents an open position as a result.
    last_index = len(pnl) - 1
    half_life_index = min(half_life, last_index)
    half_life_reached = half_life <= last_index
    expiry_reached = bool(pnl.index[-1] >= common_expiry)

    half_life_row = pnl.iloc[half_life_index]
    half_life_time = pnl.index[half_life_index]
    expiry_row = pnl.iloc[-1]
    expiry_time = pnl.index[-1]
    if strategy == "credit_spreads":
        for leg, prices in zip(leg_meta, credit_leg_prices):
            leg["half_life_price"] = round(float(prices.loc[half_life_time]), 2)
            leg["expiry_price"] = round(float(prices.loc[expiry_time]), 2)
    return {
        "points": [
            {
                "time": int(index.timestamp()),
                "pnl": round(float(row["total"]), 2),
                "pnl_pct": round(float(row["pnl_pct"]), 4),
                "legs": {name: round(float(row[name]), 2) for name in leg_series},
            }
            for index, row in pnl.iterrows()
        ],
        "legs": leg_meta,
        "x_lots": x_lots,
        "y_lots": y_lots,
        "half_life_time": int(half_life_time.timestamp()),
        "half_life_pnl": round(float(half_life_row["total"]), 2),
        "half_life_max_profit": round(float(pnl.iloc[: half_life + 1]["total"].max()), 2),
        "expiry_time": int(expiry_time.timestamp()),
        "expiry_pnl": round(float(expiry_row["total"]), 2),
        "half_life_pnl_pct": round(float(half_life_row["pnl_pct"]), 4),
        "expiry_pnl_pct": round(float(expiry_row["pnl_pct"]), 4),
        "margin": margin,
        "bars_available": len(pnl),
        "bars_requested": half_life,
        "half_life_reached": half_life_reached,
        "expiry_reached": expiry_reached,
        "contract_expiry": common_expiry.strftime("%d-%b-%Y"),
    }
