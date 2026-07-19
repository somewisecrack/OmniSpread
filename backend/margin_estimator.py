"""Conservative margin estimate using the current published NSE stock-derivative floors.

This is deliberately not presented as an exact SPAN result. It applies the current
14.2% stock price scan floor, 3.5% ELM (5.25% for >30% OTM short stock options),
recognises defined-risk option hedges, and includes net premium debit.
"""
from __future__ import annotations

from collections import defaultdict


STOCK_PRICE_SCAN_FLOOR = 0.142
INDEX_PRICE_SCAN_FLOOR = 0.093
STOCK_ELM = 0.035
INDEX_ELM = 0.02
DEEP_OTM_INDEX_ELM = 0.03
DEEP_OTM_STOCK_OPTION_ELM = 0.0525
DEEP_OTM_THRESHOLD = 0.30
SUGGESTED_BUFFER = 0.15


def _option_elm_rate(leg: dict) -> float:
    spot = float(leg["spot"])
    strike = float(leg["strike"])
    option_type = leg["instrument"]
    if option_type == "CE":
        otm = max(0.0, strike / spot - 1.0)
    else:
        otm = max(0.0, 1.0 - strike / spot)
    if leg.get("is_index"):
        return DEEP_OTM_INDEX_ELM if otm > 0.10 else INDEX_ELM
    return DEEP_OTM_STOCK_OPTION_ELM if otm > DEEP_OTM_THRESHOLD else STOCK_ELM


def _scan_floor(leg: dict) -> float:
    return INDEX_PRICE_SCAN_FLOOR if leg.get("is_index") else STOCK_PRICE_SCAN_FLOOR


SCAN_POINTS = 9


def _leg_pnl_at(leg: dict, stressed_spot: float) -> float:
    """Leg P&L if the underlying were at `stressed_spot` at expiry.

    Options are valued at intrinsic, which is the conservative reading: any
    remaining time value would only reduce the loss.
    """
    quantity = int(leg["lot_size"]) * int(leg["lots"])
    direction = 1.0 if leg["side"] == "BUY" else -1.0

    if leg["instrument"] == "FUT":
        move = stressed_spot - float(leg["spot"])
        return direction * move * quantity

    strike = float(leg["strike"])
    intrinsic = (
        max(strike - stressed_spot, 0.0) if leg["instrument"] == "PE"
        else max(stressed_spot - strike, 0.0)
    )
    return direction * (intrinsic - float(leg["price"])) * quantity


def _group_span_estimate(legs: list[dict]) -> float:
    """Worst-case loss for one underlying, scanned across its price range.

    Every leg in the group is revalued together at each scanned price, so
    futures, protective options and spreads offset one another as they actually
    would. The previous implementation looked only at the first future and the
    first option, which silently ignored every additional leg - fine for a plain
    spread, wrong for anything with more than two legs on the same underlying.
    """
    if not legs:
        return 0.0

    spot = float(legs[0]["spot"])
    scan_floor = _scan_floor(legs[0])
    strikes = [float(leg["strike"]) for leg in legs if leg["instrument"] in {"CE", "PE"}]

    if any(leg["instrument"] == "FUT" for leg in legs):
        # A futures leg loses without bound, so scan only the regulatory range.
        low, high = spot * (1.0 - scan_floor), spot * (1.0 + scan_floor)
    else:
        # Option spreads are bounded, so the true worst case can be taken rather
        # than whatever happens to fall inside the scan range. Stopping at the
        # scan range would under-charge a spread whose long leg sits outside it.
        low = min([spot, *strikes]) * 0.5
        high = max([spot, *strikes]) * 1.5

    # Payoff kinks sit at the strikes, so evaluate them explicitly as well.
    probes = [
        low + (high - low) * step / (SCAN_POINTS - 1) for step in range(SCAN_POINTS)
    ] + [strike for strike in strikes if low <= strike <= high]

    worst = min(
        sum(_leg_pnl_at(leg, stressed_spot) for leg in legs)
        for stressed_spot in probes
    )
    return max(0.0, -worst)


def estimate_margin(legs: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for leg in legs:
        grouped[str(leg["asset"])].append(leg)

    span = sum(_group_span_estimate(group) for group in grouped.values())
    elm = 0.0
    premium_balance = 0.0
    gross_notional = 0.0

    for leg in legs:
        quantity = int(leg["lot_size"]) * int(leg["lots"])
        spot = float(leg["spot"])
        notional = spot * quantity
        gross_notional += notional
        if leg["instrument"] == "FUT":
            elm += (INDEX_ELM if leg.get("is_index") else STOCK_ELM) * notional
        elif leg["side"] == "SELL":
            elm += _option_elm_rate(leg) * notional

        if leg["instrument"] in {"CE", "PE"}:
            premium = float(leg["price"]) * quantity
            premium_balance += premium if leg["side"] == "BUY" else -premium

    premium_debit = max(0.0, premium_balance)
    total = span + elm + premium_debit
    return {
        "span_estimate": round(span, 2),
        "elm": round(elm, 2),
        "premium_debit": round(premium_debit, 2),
        "gross_notional": round(gross_notional, 2),
        "estimated_margin": round(total, 2),
        "suggested_funds": round(total * (1.0 + SUGGESTED_BUFFER), 2),
        "buffer_pct": int(SUGGESTED_BUFFER * 100),
        "method": "Current NSE derivative rules (conservative estimate)",
    }
