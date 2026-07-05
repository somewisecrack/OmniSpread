"""Shared backtest compute, used by both the FastAPI service and the CLI.

The equity path pulls forward prices from yfinance; the futures / options /
credit-spread paths delegate to run_derivatives_backtest (NSE data via nselib).
Keeping the math here means the web app and the CLI produce identical numbers.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from derivatives_backtest import run_derivatives_backtest


def backtest_calendar_days(interval: str, half_life: int) -> int:
    bars_per_day = {
        "15m": 26,
        "30m": 13,
        "60m": 7,
        "1h": 7,
        "1d": 1,
    }.get(interval, 1)
    trading_days = max(3, int(half_life / bars_per_day) + 3)
    return min(370, trading_days * 3 + 10)


def close_frame(raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" in raw.columns.get_level_values(0):
            close = raw["Close"]
        elif "Adj Close" in raw.columns.get_level_values(0):
            close = raw["Adj Close"]
        else:
            return pd.DataFrame()
    else:
        close = raw[["Close"]].rename(columns={"Close": tickers[0]}) if "Close" in raw else raw
    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0])
    return close[[t for t in tickers if t in close.columns]].dropna()


def run_equity_backtest(*, x: str, y: str, qty: float, direction: str,
                        interval: str, half_life: int, end_date: str) -> dict:
    """Forward stocks-only P&L over the half-life window. Raises ValueError on no data."""
    start = datetime.strptime(end_date, "%Y-%m-%d")
    end = start + timedelta(days=backtest_calendar_days(interval, half_life))
    tickers = [x, y]

    raw = yf.download(
        tickers,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval=interval,
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    prices = close_frame(raw, tickers)
    if prices.empty or len(prices) < 2:
        raise ValueError("No forward price data was available for this pair and interval.")

    prices = prices.iloc[: half_life + 1]
    x0 = float(prices[x].iloc[0])
    y0 = float(prices[y].iloc[0])
    rows = []
    for idx, row in prices.iterrows():
        x_price = float(row[x])
        y_price = float(row[y])
        spread = y_price - qty * x_price
        if direction in {"LONG_SPREAD", "short_x_long_y"}:
            pnl_currency = -qty * (x_price - x0) + (y_price - y0)
        else:
            pnl_currency = qty * (x_price - x0) - (y_price - y0)
        rows.append({
            "time": int(idx.timestamp()),
            "x": round(x_price, 4),
            "y": round(y_price, 4),
            "spread": round(spread, 4),
            "pnl": round(float(pnl_currency), 4),
        })

    max_profit = max(row["pnl"] for row in rows)
    return {
        "interval": interval,
        "entry_time": rows[0]["time"],
        "exit_time": rows[-1]["time"],
        "final_pnl": rows[-1]["pnl"],
        "max_profit": round(float(max_profit), 4),
        "points": rows,
    }


def run_backtest(*, x: str, y: str, qty: float, direction: str, half_life: int,
                 end_date: str, strategy: str = "equity", interval: str = "1d") -> dict:
    """Dispatch to the right strategy. Returns a normalized result dict.

    Common keys across strategies: strategy, entry_time, exit_time, final_pnl,
    max_profit, points. Derivatives strategies additionally return expiry_time,
    expiry_pnl, legs, x_lots, y_lots.
    """
    if half_life < 1:
        raise ValueError("Half-life must be at least 1 bar")

    if strategy == "equity":
        result = run_equity_backtest(
            x=x, y=y, qty=qty, direction=direction,
            interval=interval, half_life=half_life, end_date=end_date,
        )
        result["strategy"] = "equity"
        return result

    if interval != "1d":
        raise ValueError("Futures and options backtests are available only for daily scans.")

    from nselib import derivatives

    result = run_derivatives_backtest(
        x=x, y=y, qty=qty, direction=direction, half_life=half_life,
        end_date=end_date, strategy=strategy,
        fetch_future=derivatives.future_price_volume_data,
        fetch_option=derivatives.option_price_volume_data,
    )
    rows = result["points"]
    return {
        "strategy": strategy,
        "interval": "1d",
        "entry_time": rows[0]["time"],
        "exit_time": result["half_life_time"],
        "final_pnl": result["half_life_pnl"],
        "max_profit": result["half_life_max_profit"],
        "final_pnl_pct": result["half_life_pnl_pct"],
        "expiry_time": result["expiry_time"],
        "expiry_pnl": result["expiry_pnl"],
        "expiry_pnl_pct": result["expiry_pnl_pct"],
        "margin": result["margin"],
        "points": rows,
        "legs": result["legs"],
        "x_lots": result["x_lots"],
        "y_lots": result["y_lots"],
    }
