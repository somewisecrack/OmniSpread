import pandas as pd

from derivatives_backtest import (
    build_credit_spread_structure,
    run_derivatives_backtest,
    whole_lot_hedge,
)


DATES = ["01-Jun-2026", "02-Jun-2026", "03-Jun-2026", "04-Jun-2026", "30-Jun-2026"]
EXPIRIES = ["02-Jun-2026", "30-Jun-2026"]


def _future_frame(symbol: str, lot: int, start_price: float) -> pd.DataFrame:
    rows = []
    for expiry in EXPIRIES:
        for index, date in enumerate(DATES):
            rows.append({
                "TIMESTAMP": date,
                "EXPIRY_DT": expiry,
                "SYMBOL": symbol,
                "OPTION_TYPE": "XX",
                "STRIKE_PRICE": 0,
                "CLOSING_PRICE": start_price + index,
                "UNDERLYING_VALUE": start_price,
                "MARKET_LOT": lot,
            })
    return pd.DataFrame(rows)


def _option_frame(symbol: str, lot: int, spot: float) -> pd.DataFrame:
    rows = []
    strikes = list(range(int(spot * 0.5), int(spot * 1.51), 10))
    for expiry in EXPIRIES:
        for date_index, date in enumerate(DATES):
            for option_type in ("PE", "CE"):
                for strike in strikes:
                    rows.append({
                        "TIMESTAMP": date,
                        "EXPIRY_DT": expiry,
                        "SYMBOL": symbol,
                        "OPTION_TYPE": option_type,
                        "STRIKE_PRICE": strike,
                        "CLOSING_PRICE": 20 + date_index + abs(strike - spot) / 100,
                        "UNDERLYING_VALUE": spot,
                        "MARKET_LOT": lot,
                    })
    return pd.DataFrame(rows)


def _fetch_future(**kwargs):
    if kwargs["symbol"] == "AAA":
        return _future_frame("AAA", 25, 100)
    return _future_frame("BBB", 10, 200)


def _fetch_option(**kwargs):
    if kwargs["symbol"] == "AAA":
        return _option_frame("AAA", 25, 100)
    return _option_frame("BBB", 10, 200)


def test_whole_lot_hedge_approximates_share_ratio():
    x_lots, y_lots = whole_lot_hedge(1.5, 25, 10)
    assert (x_lots, y_lots) == (3, 5)
    assert x_lots * 25 / (y_lots * 10) == 1.5


def test_futures_options_selects_expiry_after_exit_and_protective_options():
    result = run_derivatives_backtest(
        x="AAA.NS", y="BBB.NS", qty=1.5, direction="SHORT_SPREAD",
        half_life=2, end_date="2026-06-01", strategy="futures_options",
        fetch_future=_fetch_future, fetch_option=_fetch_option,
    )
    assert (result["x_lots"], result["y_lots"]) == (3, 5)
    assert len(result["points"]) == 5
    assert result["half_life_time"] == int(pd.Timestamp("2026-06-03").timestamp())
    assert result["expiry_time"] == int(pd.Timestamp("2026-06-30").timestamp())
    assert {leg["expiry"] for leg in result["legs"]} == {"30-Jun-2026"}
    option_legs = [leg for leg in result["legs"] if leg["instrument"] in {"PE", "CE"}]
    assert [(leg["symbol"], leg["side"], leg["instrument"], leg["strike"]) for leg in option_legs] == [
        ("AAA", "BUY", "PE", 100.0),
        ("BBB", "BUY", "CE", 200.0),
    ]


def test_credit_spreads_buy_hedges_are_three_strikes_further_otm():
    result = run_derivatives_backtest(
        x="AAA.NS", y="BBB.NS", qty=1.5, direction="SHORT_SPREAD",
        half_life=2, end_date="2026-06-01", strategy="credit_spreads",
        fetch_future=_fetch_future, fetch_option=_fetch_option,
    )
    x_legs = [leg for leg in result["legs"] if leg["asset"] == "x"]
    y_legs = [leg for leg in result["legs"] if leg["asset"] == "y"]
    assert x_legs[0]["side"] == "SELL" and x_legs[0]["strike"] - x_legs[1]["strike"] == 30
    assert y_legs[0]["side"] == "SELL" and y_legs[1]["strike"] - y_legs[0]["strike"] == 30
    assert result["points"][0]["pnl"] == 0


def test_current_credit_structure_uses_whole_lots_and_correct_spread_sides():
    result = build_credit_spread_structure(
        x="AAA.NS", y="BBB.NS", qty=1.5, direction="SHORT_SPREAD",
        fetch_future=_fetch_future, fetch_option=_fetch_option,
        as_of_date=pd.Timestamp("2026-06-30").to_pydatetime(),
    )
    assert (result["x_lots"], result["y_lots"]) == (3, 5)
    assert result["actual_ratio"] == 1.5
    assert [(leg["asset"], leg["side"], leg["instrument"]) for leg in result["legs"]] == [
        ("x", "SELL", "PE"), ("x", "BUY", "PE"),
        ("y", "SELL", "CE"), ("y", "BUY", "CE"),
    ]
