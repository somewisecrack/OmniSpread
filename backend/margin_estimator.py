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


def _group_span_estimate(legs: list[dict]) -> float:
    futures = [leg for leg in legs if leg["instrument"] == "FUT"]
    options = [leg for leg in legs if leg["instrument"] in {"CE", "PE"}]

    if futures and not options:
        return sum(
            _scan_floor(leg) * float(leg["spot"]) * int(leg["lot_size"]) * int(leg["lots"])
            for leg in futures
        )

    if futures and options:
        future = futures[0]
        option = options[0]
        quantity = int(future["lot_size"]) * int(future["lots"])
        spot = float(future["spot"])
        scan_floor = _scan_floor(future)
        stressed_spot = spot * (
            1.0 - scan_floor if future["side"] == "BUY"
            else 1.0 + scan_floor
        )
        future_pnl = (
            stressed_spot - spot if future["side"] == "BUY"
            else spot - stressed_spot
        ) * quantity
        strike = float(option["strike"])
        intrinsic = (
            max(strike - stressed_spot, 0.0) if option["instrument"] == "PE"
            else max(stressed_spot - strike, 0.0)
        )
        option_pnl = (intrinsic - float(option["price"])) * quantity
        return max(0.0, -(future_pnl + option_pnl))

    short_options = [leg for leg in options if leg["side"] == "SELL"]
    long_options = [leg for leg in options if leg["side"] == "BUY"]
    if short_options and long_options:
        short = short_options[0]
        hedge = long_options[0]
        quantity = int(short["lot_size"]) * int(short["lots"])
        width = abs(float(short["strike"]) - float(hedge["strike"]))
        net_credit = float(short["price"]) - float(hedge["price"])
        return max(0.0, (width - net_credit) * quantity)

    return 0.0


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
