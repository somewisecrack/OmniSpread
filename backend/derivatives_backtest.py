from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd
from margin_estimator import estimate_margin


FetchFuture = Callable[..., pd.DataFrame]
FetchOption = Callable[..., pd.DataFrame]


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
    for column in ("CLOSING_PRICE", "STRIKE_PRICE", "MARKET_LOT", "UNDERLYING_VALUE"):
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
        offset = -0.02 if option_type == "PE" else 0.02
        hedge_steps = -3 if option_type == "PE" else 3
        sold = _choose_option(
            asset["options"], asset["expiry"], option_type, asset["spot"], offset,
            snapshot=asset["option_snapshot"],
        )
        hedge = _choose_option(
            asset["options"], asset["expiry"], option_type, asset["spot"], offset,
            hedge_steps, snapshot=asset["option_snapshot"],
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
) -> dict:
    start = datetime.strptime(end_date, "%Y-%m-%d")
    # Half-life is measured in daily trading bars for derivatives.
    required_expiry = pd.Timestamp(start) + pd.offsets.BDay(half_life)
    # Fetch far enough to include the monthly contract's expiry bar as well as
    # the half-life exit. The nearest eligible monthly expiry can be several
    # weeks after the required exit date.
    fetch_end = max(
        start + timedelta(days=half_life * 3 + 14),
        required_expiry.to_pydatetime() + timedelta(days=40),
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
            offset = -0.02 if opt_type == "PE" else 0.02
            contract = _choose_option(asset["options"], expiry, opt_type, asset["spot"], offset)
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
            offset = -0.02 if opt_type == "PE" else 0.02
            hedge_steps = -3 if opt_type == "PE" else 3
            sold = _choose_option(asset["options"], expiry, opt_type, asset["spot"], offset)
            hedge = _choose_option(
                asset["options"], expiry, opt_type, asset["spot"], offset, hedge_steps
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
    half_life_row = pnl.iloc[min(half_life, len(pnl) - 1)]
    half_life_time = pnl.index[min(half_life, len(pnl) - 1)]
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
    }
