import pandas as pd

from derivatives_backtest import (
    STRIKE_RULE_VOL,
    _clean,
    _credit_spread_contracts,
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


def _fetch_future_without_underlying(**kwargs):
    frame = _fetch_future(**kwargs)
    frame["UNDERLYING_VALUE"] = None
    return frame


def _fetch_option_without_underlying(**kwargs):
    frame = _fetch_option(**kwargs)
    frame["UNDERLYING_VALUE"] = None
    return frame


def test_whole_lot_hedge_approximates_share_ratio():
    x_lots, y_lots = whole_lot_hedge(1.5, 25, 10)
    assert (x_lots, y_lots) == (3, 5)
    assert x_lots * 25 / (y_lots * 10) == 1.5


def test_whole_lot_hedge_prefers_compact_approximations():
    assert whole_lot_hedge(10 / 13, 1, 1) == (3, 4)
    assert whole_lot_hedge(9 / 10, 1, 1) == (1, 1)
    assert whole_lot_hedge(0.8, 1, 1) == (4, 5)


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
    assert result["points"][0]["pnl_pct"] == 0
    assert all("half_life_price" in leg and "expiry_price" in leg for leg in result["legs"])
    assert [leg["half_life_price"] for leg in result["legs"]] == [22.0, 22.3, 22.0, 22.3]
    assert [leg["expiry_price"] for leg in result["legs"]] == [24.0, 24.3, 24.0, 24.3]
    assert result["margin"]["estimated_margin"] > 0
    assert result["half_life_pnl_pct"] == round(
        result["half_life_pnl"] * 100 / result["margin"]["estimated_margin"], 4
    )


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
    assert result["margin"]["estimated_margin"] > 0
    assert result["margin"]["suggested_funds"] > result["margin"]["estimated_margin"]


VOL_SPOT = 1000.0
VOL_EXPIRY = "01-Jul-2026"   # 30 days after the entry date below
VOL_ENTRY = "01-Jun-2026"


def _vol_option_frame(atm_call: float = 30.0, atm_call_volume: float = 500.0) -> pd.DataFrame:
    """Chain with a realistic ~6%-of-spot ATM straddle and a 20-point ladder."""
    rows = []
    for strike in range(700, 1301, 20):
        for option_type in ("PE", "CE"):
            distance = abs(strike - VOL_SPOT)
            price = max(30.0 - distance * 0.02, 1.0)
            volume = 500.0
            if strike == VOL_SPOT and option_type == "CE":
                price, volume = atm_call, atm_call_volume
            rows.append({
                "TIMESTAMP": VOL_ENTRY,
                "EXPIRY_DT": VOL_EXPIRY,
                "SYMBOL": "AAA",
                "OPTION_TYPE": option_type,
                "STRIKE_PRICE": strike,
                "CLOSING_PRICE": price,
                "UNDERLYING_VALUE": VOL_SPOT,
                "MARKET_LOT": 25,
                "TOT_TRADED_QTY": volume,
            })
    return _clean(pd.DataFrame(rows))


def test_vol_strike_rule_places_strikes_in_expected_move_units():
    # ATM straddle 30 + 30 = 60 -> 1 SD move = 60 * 1.2533 = 75.2
    # PE sold  ~ 1000 - 75.2  = 924.8 -> nearest listed strike 920
    # PE hedge ~ 1000 - 131.6 = 868.4 -> nearest listed strike 860
    options = _vol_option_frame()
    expiry = pd.Timestamp(VOL_EXPIRY)
    sold, hedge = _credit_spread_contracts(
        options, expiry, "PE", VOL_SPOT, strike_rule=STRIKE_RULE_VOL,
    )
    assert (sold.strike, hedge.strike) == (920.0, 860.0)

    sold_ce, hedge_ce = _credit_spread_contracts(
        options, expiry, "CE", VOL_SPOT, strike_rule=STRIKE_RULE_VOL,
    )
    assert (sold_ce.strike, hedge_ce.strike) == (1080.0, 1140.0)


def test_vol_strike_rule_widens_with_a_larger_straddle():
    """A richer ATM straddle must push both strikes further out."""
    calm = _vol_option_frame()
    stormy = _vol_option_frame(atm_call=60.0)  # straddle 90 instead of 60
    expiry = pd.Timestamp(VOL_EXPIRY)
    calm_sold, _ = _credit_spread_contracts(
        calm, expiry, "PE", VOL_SPOT, strike_rule=STRIKE_RULE_VOL)
    storm_sold, _ = _credit_spread_contracts(
        stormy, expiry, "PE", VOL_SPOT, strike_rule=STRIKE_RULE_VOL)
    assert storm_sold.strike < calm_sold.strike


def test_vol_strike_rule_repairs_a_stale_atm_leg_via_put_call_parity():
    """An untraded ATM call must not inflate the expected move.

    A stale 200.0 call would imply a 230 straddle (1 SD = 288) and drag the
    sold put down to ~720. Parity repair rebuilds the call from the traded put,
    keeping the sold strike at 920.
    """
    options = _vol_option_frame(atm_call=200.0, atm_call_volume=0.0)
    sold, _ = _credit_spread_contracts(
        options, pd.Timestamp(VOL_EXPIRY), "PE", VOL_SPOT, strike_rule=STRIKE_RULE_VOL,
    )
    assert sold.strike == 920.0


def test_legacy_strike_rule_is_the_default_and_unchanged():
    options = _vol_option_frame()
    expiry = pd.Timestamp(VOL_EXPIRY)
    sold, hedge = _credit_spread_contracts(options, expiry, "PE", VOL_SPOT)
    # 2% OTM -> 980, hedge three listed 20-point strikes further out -> 920
    assert (sold.strike, hedge.strike) == (980.0, 920.0)


def test_current_credit_structure_falls_back_to_futures_close_when_spot_is_missing():
    result = build_credit_spread_structure(
        x="AAA.NS", y="BBB.NS", qty=1.5, direction="SHORT_SPREAD",
        fetch_future=_fetch_future_without_underlying,
        fetch_option=_fetch_option_without_underlying,
        as_of_date=pd.Timestamp("2026-06-30").to_pydatetime(),
    )
    assert [leg["spot"] for leg in result["legs"] if leg["asset"] == "x"] == [104.0, 104.0]
    assert [leg["spot"] for leg in result["legs"] if leg["asset"] == "y"] == [204.0, 204.0]
