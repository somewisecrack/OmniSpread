#!/usr/bin/env python3
"""
OmniSpread CLI — run the pairs-trading scanner from the terminal.

Reuses the same OmniSpreadEngine that powers the FastAPI service, so results
are identical to the web app without needing a running server or browser.

Examples:
    python3 cli.py presets
    python3 cli.py scan --preset mega_tech
    python3 cli.py scan --tickers AAPL MSFT NVDA --period 2y --interval 1d
    python3 cli.py scan --preset financials --min-prob 60 --limit 10
    python3 cli.py scan --preset energy --json results.json
"""
import argparse
import json
import sys

from engine import OmniSpreadEngine
from backtest_runner import run_backtest
from presets import PRESETS

STRATEGIES = ["equity", "futures", "futures_options", "credit_spreads"]
STRATEGY_LABELS = {
    "equity": "Stocks only",
    "futures": "Futures only",
    "futures_options": "Futures + options",
    "credit_spreads": "Credit spreads",
}

# Columns rendered in the results table: (result key, header, width, alignment)
COLUMNS = [
    ("pair", "PAIR", 22, "<"),
    ("combo", "COMBO", 10, "<"),
    ("method", "METHOD", 8, "<"),
    ("z_score", "Z", 7, ">"),
    ("half_life", "HL", 5, ">"),
    ("hurst", "HURST", 6, ">"),
    ("prob_profit", "P(WIN)%", 8, ">"),
    ("exp_return", "EXP.RET", 9, ">"),
    ("same_sector", "SECTOR", 7, "<"),
    ("extreme_z_in_hl", "EXT.Z", 6, "<"),
]


def _fmt_cell(value, width, align):
    if isinstance(value, float):
        text = f"{value:.2f}"
    else:
        text = str(value)
    if len(text) > width:
        text = text[: width - 1] + "…"
    return f"{text:{align}{width}}"


def _render_table(results):
    header = "  ".join(_fmt_cell(h, w, a) for _, h, w, a in COLUMNS)
    print(header)
    print("-" * len(header))
    for r in results:
        row = "  ".join(_fmt_cell(r.get(key, ""), w, a) for key, _, w, a in COLUMNS)
        print(row)


def cmd_presets(_args):
    print("Available presets:\n")
    width = max(len(name) for name in PRESETS)
    for name, tickers in PRESETS.items():
        print(f"  {name:<{width}}  {len(tickers):>3} tickers")
    print("\nUse with:  scan --preset <name>")
    return 0


def cmd_scan(args):
    if args.preset:
        if args.preset not in PRESETS:
            print(f"error: unknown preset '{args.preset}'. Run 'presets' to list them.", file=sys.stderr)
            return 2
        tickers = PRESETS[args.preset]
        source = f"preset '{args.preset}'"
    else:
        tickers = [t.upper() if not t.startswith("^") else t for t in args.tickers]
        source = "custom tickers"

    if len(tickers) < 2:
        print("error: need at least 2 tickers to form a pair.", file=sys.stderr)
        return 2

    print(f"Scanning {len(tickers)} tickers from {source} "
          f"(period={args.period}, interval={args.interval})...", file=sys.stderr)

    engine = OmniSpreadEngine(
        tickers=tickers,
        period=args.period,
        interval=args.interval,
        start_date=args.start,
        end_date=args.end,
        top_n=args.top_n,
    )

    try:
        results = engine.run_scan()
    except Exception as exc:  # surface engine failures cleanly instead of a traceback
        print(f"error: scan failed: {exc}", file=sys.stderr)
        return 1

    if args.min_prob is not None:
        results = [r for r in results if r.get("prob_profit", 0) >= args.min_prob]

    if args.limit:
        results = results[: args.limit]

    if args.json is not None:
        payload = json.dumps(results, indent=2, default=str)
        if args.json == "-":
            print(payload)
        else:
            with open(args.json, "w") as fh:
                fh.write(payload)
            print(f"Wrote {len(results)} pairs to {args.json}", file=sys.stderr)
        return 0

    if not results:
        print("\nNo cointegrated pairs matched the criteria.", file=sys.stderr)
        return 0

    print(f"\nTop {len(results)} pairs by P(profit):\n")
    _render_table(results)
    return 0


def cmd_backtest(args):
    strategies = STRATEGIES if args.strategy == "all" else [args.strategy]

    print(f"Backtesting {args.x}/{args.y}  qty={args.qty}  dir={args.direction}  "
          f"HL={args.half_life}  entry={args.end_date}", file=sys.stderr)

    rows = []
    for strat in strategies:
        print(f"  running {strat}...", file=sys.stderr)
        try:
            res = run_backtest(
                x=args.x, y=args.y, qty=args.qty, direction=args.direction,
                half_life=args.half_life, end_date=args.end_date,
                strategy=strat, interval=args.interval,
            )
            rows.append({
                "strategy": strat,
                "status": "ok",
                "final_pnl": res["final_pnl"],
                "max_profit": res["max_profit"],
                "expiry_pnl": res.get("expiry_pnl"),
                "bars": len(res["points"]),
                "lots": (f"{res['x_lots']}x/{res['y_lots']}y" if "x_lots" in res else "-"),
            })
        except Exception as exc:
            rows.append({"strategy": strat, "status": "fail", "error": str(exc)})

    if args.json is not None:
        payload = json.dumps(rows, indent=2, default=str)
        if args.json == "-":
            print(payload)
        else:
            with open(args.json, "w") as fh:
                fh.write(payload)
            print(f"Wrote backtest results to {args.json}", file=sys.stderr)
        return 0

    cols = [
        ("strategy", "STRATEGY", 18, "<"),
        ("final_pnl", "HL P&L (₹)", 12, ">"),
        ("max_profit", "MAX PROFIT", 12, ">"),
        ("expiry_pnl", "EXPIRY P&L", 12, ">"),
        ("bars", "BARS", 5, ">"),
        ("lots", "LOTS", 9, "<"),
        ("status", "STATUS", 6, "<"),
    ]
    print(f"\n{args.x}/{args.y}  —  all strategies:\n")
    header = "  ".join(_fmt_cell(h, w, a) for _, h, w, a in cols)
    print(header)
    print("-" * len(header))
    for r in rows:
        label = STRATEGY_LABELS.get(r["strategy"], r["strategy"])
        if r["status"] == "fail":
            line = "  ".join([
                _fmt_cell(label, 18, "<"),
                _fmt_cell(f"— {r['error']}", len(header) - 20, "<"),
            ])
            print(line)
            continue
        cells = {
            "strategy": label,
            "final_pnl": r["final_pnl"],
            "max_profit": r["max_profit"],
            "expiry_pnl": r["expiry_pnl"] if r["expiry_pnl"] is not None else "-",
            "bars": r["bars"],
            "lots": r["lots"],
            "status": "ok",
        }
        print("  ".join(_fmt_cell(cells[key], w, a) for key, _, w, a in cols))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="omnispread",
        description="Statistical pairs-trading scanner (Kalman cointegration + Monte Carlo).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_presets = sub.add_parser("presets", help="List built-in ticker presets")
    p_presets.set_defaults(func=cmd_presets)

    p_scan = sub.add_parser("scan", help="Scan a universe of tickers for cointegrated pairs")
    src = p_scan.add_mutually_exclusive_group(required=True)
    src.add_argument("--preset", help="Use a built-in preset (see 'presets')")
    src.add_argument("--tickers", nargs="+", metavar="SYM", help="Explicit list of tickers")
    p_scan.add_argument("--period", default="3y", help="Lookback period, e.g. 1y, 2y, 3y, 60d (default: 3y)")
    p_scan.add_argument("--interval", default="1d", help="Bar interval, e.g. 1d, 60m, 30m, 15m (default: 1d)")
    p_scan.add_argument("--start", help="Start date YYYY-MM-DD (overrides --period when used with --end)")
    p_scan.add_argument("--end", help="End date YYYY-MM-DD")
    p_scan.add_argument("--top-n", type=int, default=50, help="Max cointegrated pairs to run MC on (default: 50)")
    p_scan.add_argument("--min-prob", type=float, help="Only show pairs with P(profit) >= this percent")
    p_scan.add_argument("--limit", type=int, help="Show only the first N pairs")
    p_scan.add_argument("--json", nargs="?", const="-", metavar="FILE",
                        help="Output JSON instead of a table; optional FILE path (default: stdout)")
    p_scan.set_defaults(func=cmd_scan)

    p_bt = sub.add_parser("backtest", help="Backtest a single pair across strategy types")
    p_bt.add_argument("--x", required=True, help="First leg ticker (e.g. ITC.NS)")
    p_bt.add_argument("--y", required=True, help="Second leg ticker (e.g. DRREDDY.NS)")
    p_bt.add_argument("--qty", type=float, required=True, help="Hedge ratio (X shares per Y share)")
    p_bt.add_argument("--direction", default="SHORT_SPREAD",
                      choices=["SHORT_SPREAD", "LONG_SPREAD", "long_x_short_y", "short_x_long_y"],
                      help="Spread direction (default: SHORT_SPREAD)")
    p_bt.add_argument("--half-life", type=int, required=True, help="Exit horizon in bars")
    p_bt.add_argument("--end-date", required=True, metavar="YYYY-MM-DD", help="Entry date")
    p_bt.add_argument("--interval", default="1d", help="Bar interval (equity only; derivatives are 1d)")
    p_bt.add_argument("--strategy", default="all",
                      choices=["all"] + STRATEGIES,
                      help="Strategy to run, or 'all' for every type (default: all)")
    p_bt.add_argument("--json", nargs="?", const="-", metavar="FILE",
                      help="Output JSON instead of a table")
    p_bt.set_defaults(func=cmd_backtest)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
