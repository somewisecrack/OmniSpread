from margin_estimator import estimate_margin


def _leg(**overrides):
    leg = {
        "asset": "x",
        "instrument": "FUT",
        "side": "BUY",
        "lots": 2,
        "lot_size": 100,
        "spot": 500.0,
        "price": 505.0,
        "is_index": False,
    }
    leg.update(overrides)
    return leg


def test_futures_margin_uses_stock_scan_floor_and_elm():
    margin = estimate_margin([_leg()])
    assert margin["span_estimate"] == 14_200
    assert margin["elm"] == 3_500
    assert margin["estimated_margin"] == 17_700
    assert margin["suggested_funds"] == 20_355


def test_credit_spread_span_is_defined_risk_and_includes_short_option_elm():
    legs = [
        _leg(instrument="PE", side="SELL", strike=490, price=12),
        _leg(instrument="PE", side="BUY", strike=460, price=5),
    ]
    margin = estimate_margin(legs)
    assert margin["span_estimate"] == (30 - 7) * 200
    assert margin["elm"] == 3_500
    assert margin["premium_debit"] == 0
    assert margin["estimated_margin"] == 8_100


def test_protective_put_reduces_futures_scan_loss_but_premium_debit_is_funded():
    legs = [
        _leg(),
        _leg(instrument="PE", side="BUY", strike=490, price=8),
    ]
    margin = estimate_margin(legs)
    # At the 14.2% adverse scan point the future loses 14,200 and the put,
    # net of its premium, gains 10,600.
    assert margin["span_estimate"] == 3_600
    assert margin["elm"] == 3_500
    assert margin["premium_debit"] == 1_600
    assert margin["estimated_margin"] == 8_700
