import pandas as pd

from derivatives_backtest import (
    STRIKE_RULE_LEGACY,
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


def test_futures_options_uses_nearest_expiry_and_vol_scaled_protection():
    """Nearest expiry (02-Jun) is taken even though it precedes the half-life exit.

    The protective options are placed one expected move out rather than a flat
    2% of spot: with an ATM straddle of 40 the move is ~50, so AAA (spot 100)
    protects at 50 PE and BBB (spot 200) at 250 CE.
    """
    result = run_derivatives_backtest(
        x="AAA.NS", y="BBB.NS", qty=1.5, direction="SHORT_SPREAD",
        half_life=2, end_date="2026-06-01", strategy="futures_options",
        fetch_future=_fetch_future, fetch_option=_fetch_option,
    )
    assert (result["x_lots"], result["y_lots"]) == (3, 5)
    assert {leg["expiry"] for leg in result["legs"]} == {"02-Jun-2026"}
    # Half-life of 2 bars outlives the contract, so the trade exits at expiry.
    assert result["half_life_time"] == int(pd.Timestamp("2026-06-02").timestamp())
    assert result["expiry_time"] == int(pd.Timestamp("2026-06-02").timestamp())
    option_legs = [leg for leg in result["legs"] if leg["instrument"] in {"PE", "CE"}]
    assert [(leg["symbol"], leg["side"], leg["instrument"], leg["strike"]) for leg in option_legs] == [
        ("AAA", "BUY", "PE", 50.0),
        ("BBB", "BUY", "CE", 250.0),
    ]


def test_credit_spreads_buy_hedges_are_three_strikes_further_otm():
    """Legacy rule, now opt-in: sold 2% OTM, hedge three listed strikes further."""
    result = run_derivatives_backtest(
        x="AAA.NS", y="BBB.NS", qty=1.5, direction="SHORT_SPREAD",
        half_life=2, end_date="2026-06-01", strategy="credit_spreads",
        fetch_future=_fetch_future, fetch_option=_fetch_option,
        strike_rule=STRIKE_RULE_LEGACY,
    )
    x_legs = [leg for leg in result["legs"] if leg["asset"] == "x"]
    y_legs = [leg for leg in result["legs"] if leg["asset"] == "y"]
    assert x_legs[0]["side"] == "SELL" and x_legs[0]["strike"] - x_legs[1]["strike"] == 30
    assert y_legs[0]["side"] == "SELL" and y_legs[1]["strike"] - y_legs[0]["strike"] == 30
    assert result["points"][0]["pnl"] == 0
    assert result["points"][0]["pnl_pct"] == 0
    assert all("half_life_price" in leg and "expiry_price" in leg for leg in result["legs"])
    # Nearest expiry is 02-Jun, so the half-life exit and expiry coincide.
    assert [leg["half_life_price"] for leg in result["legs"]] == [21.0, 21.3, 21.0, 21.3]
    assert [leg["expiry_price"] for leg in result["legs"]] == [21.0, 21.3, 21.0, 21.3]
    assert result["margin"]["estimated_margin"] > 0
    assert result["half_life_pnl_pct"] == round(
        result["half_life_pnl"] * 100 / result["margin"]["estimated_margin"], 4
    )


def test_nearest_expiry_is_used_even_when_shorter_than_the_half_life():
    """A 20-bar half-life must not skip the 02-Jun contract for the 30-Jun one.

    Holding a far-dated contract for a short trade gives away most of the time
    decay, so the nearest expiry wins and the trade exits when it expires.
    """
    result = run_derivatives_backtest(
        x="AAA.NS", y="BBB.NS", qty=1.5, direction="SHORT_SPREAD",
        half_life=20, end_date="2026-06-01", strategy="credit_spreads",
        fetch_future=_fetch_future, fetch_option=_fetch_option,
    )
    assert {leg["expiry"] for leg in result["legs"]} == {"02-Jun-2026"}
    assert result["half_life_time"] == result["expiry_time"]
    assert result["half_life_time"] == int(pd.Timestamp("2026-06-02").timestamp())


def test_group_span_nets_every_leg_on_the_same_underlying():
    """A short spread against a future must reduce the scanned worst case.

    The previous estimator read only futures[0] and options[0], so any leg
    beyond the first two was silently ignored and a four-leg group priced
    identically to a bare protected future.
    """
    from margin_estimator import _group_span_estimate

    spot, lot = 1000.0, 100
    def leg(instrument, side, strike, price):
        return {"asset": "x", "symbol": "AAA", "instrument": instrument, "side": side,
                "lots": 1, "lot_size": lot, "strike": strike, "spot": spot, "price": price}

    future_only = [leg("FUT", "BUY", 0, spot)]
    protected = future_only + [leg("PE", "BUY", 950, 12.0)]
    multi_leg = protected + [leg("CE", "SELL", 1060, 8.0), leg("CE", "BUY", 1100, 3.0)]

    assert _group_span_estimate(protected) < _group_span_estimate(future_only)
    # The short call spread brings in premium, lowering the worst case further.
    assert _group_span_estimate(multi_leg) < _group_span_estimate(protected)


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
    """Chain with a realistic ~6%-of-spot ATM straddle and a 20-point ladder.

    Premium decays convexly away from the money (halving every 40 points), as a
    real chain does. A linear decay would make a wider hedge unconditionally
    better and the credit-to-margin optimum would sit at the search ceiling.
    """
    rows = []
    for strike in range(700, 1301, 20):
        for option_type in ("PE", "CE"):
            distance = abs(strike - VOL_SPOT)
            price = max(30.0 * (0.5 ** (distance / 40.0)), 0.05)
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


def test_vol_strike_rule_is_the_default():
    """Credit spreads are volatility-scaled unless legacy is asked for."""
    options = _vol_option_frame()
    expiry = pd.Timestamp(VOL_EXPIRY)
    sold, hedge = _credit_spread_contracts(options, expiry, "PE", VOL_SPOT)
    assert (sold.strike, hedge.strike) == (920.0, 860.0)


def test_hedge_maximises_credit_per_rupee_of_margin():
    """The chosen hedge must beat every other tradeable strike on credit/margin."""
    from margin_estimator import estimate_margin

    options = _vol_option_frame()
    expiry = pd.Timestamp(VOL_EXPIRY)
    sold, hedge = _credit_spread_contracts(options, expiry, "PE", VOL_SPOT)

    entry = options[(options["OPTION_TYPE"] == "PE") & options["STRIKE_PRICE"].notna()]
    price_of = dict(zip(entry["STRIKE_PRICE"], entry["CLOSING_PRICE"]))
    sold_price = price_of[sold.strike]

    def ratio(strike):
        credit = sold_price - price_of[strike]
        if credit <= 0:
            return -1.0
        legs = [
            {"asset": "x", "symbol": "AAA", "instrument": "PE", "side": "SELL", "lots": 1,
             "lot_size": 25, "strike": sold.strike, "spot": VOL_SPOT, "price": sold_price},
            {"asset": "x", "symbol": "AAA", "instrument": "PE", "side": "BUY", "lots": 1,
             "lot_size": 25, "strike": strike, "spot": VOL_SPOT, "price": price_of[strike]},
        ]
        return credit / estimate_margin(legs)["estimated_margin"]

    ceiling = VOL_SPOT - 2.5 * 75.2   # DEFAULT_HEDGE_SD expected moves
    candidates = [s for s in price_of if ceiling <= s < sold.strike]
    assert len(candidates) > 3, "need several candidates for this to mean anything"
    assert ratio(hedge.strike) == max(ratio(s) for s in candidates)


def test_hedge_search_ignores_strikes_that_never_traded():
    """A stale mark must not win the search just because it looks cheap.

    An untraded strike carries a settlement price, not a quote. Priced at 0.01
    it would offer the best credit-to-margin ratio of any candidate, so the
    search has to skip it on volume rather than price.
    """
    frame = _vol_option_frame()
    bait = (frame["STRIKE_PRICE"] == 880) & (frame["OPTION_TYPE"] == "PE")
    frame.loc[bait, "CLOSING_PRICE"] = 0.01
    frame.loc[bait, "TOT_TRADED_QTY"] = 0

    sold, hedge = _credit_spread_contracts(frame, pd.Timestamp(VOL_EXPIRY), "PE", VOL_SPOT)
    assert hedge.strike != 880


def test_vol_rule_falls_back_to_legacy_on_a_chain_it_cannot_express():
    """A 40%-of-spot straddle pushes 1 SD past the outermost strike.

    The structure must still be produced (via the legacy offset) rather than
    failing outright.
    """
    options = _clean(_option_frame("AAA", 25, 100.0))
    expiry = pd.Timestamp("30-Jun-2026")
    sold, hedge = _credit_spread_contracts(options, expiry, "PE", 100.0)
    assert sold.strike != hedge.strike
    assert sold.strike - hedge.strike == 30  # legacy three-strike wing


def test_legacy_strike_rule_still_available_on_request():
    options = _vol_option_frame()
    expiry = pd.Timestamp(VOL_EXPIRY)
    sold, hedge = _credit_spread_contracts(
        options, expiry, "PE", VOL_SPOT, strike_rule=STRIKE_RULE_LEGACY,
    )
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


def test_blank_option_lot_filled_from_futures():
    from derivatives_backtest import _fill_option_lots

    d1, d2 = pd.Timestamp("2024-09-30"), pd.Timestamp("2024-10-01")
    exp = pd.Timestamp("2024-10-31")
    futures = pd.DataFrame({"date": [d1, d2], "expiry": [exp, exp], "MARKET_LOT": [350.0, 350.0]})
    options = pd.DataFrame({
        "date": [d1, d1, d2],
        "expiry": [exp, exp, exp],
        "STRIKE_PRICE": [3000.0, 3100.0, 3000.0],
        "MARKET_LOT": [350.0, float("nan"), float("nan")],
    })
    filled = _fill_option_lots(options, futures)
    assert filled["MARKET_LOT"].tolist() == [350.0, 350.0, 350.0]
    assert options["MARKET_LOT"].isna().sum() == 2  # input not mutated


def test_missing_lot_gives_clear_error():
    import pytest
    from derivatives_backtest import _lot

    assert _lot(350.0) == 350
    with pytest.raises(ValueError, match="missing the market lot size"):
        _lot(float("nan"), "M&M")
