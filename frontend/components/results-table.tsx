"use client";

import {
    getCreditStructure,
    type BacktestStrategy,
    type CreditStructureResult,
    type PairResult,
} from "@/lib/api";
import { useState } from "react";

interface ResultsTableProps {
    results: PairResult[];
    isLoading: boolean;
    onRowClick: (pair: PairResult) => void;
    interval: string;
    endDate: string;
}

type SortKey = keyof PairResult;

// Count trading days (Mon–Fri) strictly after `dateStr` up to and including today.
// An empty/invalid date (period scans "up to now") counts as 0 → treated as current.
const tradingDaysSince = (dateStr: string): number => {
    if (!dateStr) return 0;
    const end = new Date(`${dateStr}T00:00:00`);
    if (Number.isNaN(end.getTime())) return 0;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    let count = 0;
    const cursor = new Date(end);
    cursor.setDate(cursor.getDate() + 1);
    while (cursor <= today) {
        const day = cursor.getDay();
        if (day !== 0 && day !== 6) count += 1;
        cursor.setDate(cursor.getDate() + 1);
    }
    return count;
};

export default function ResultsTable({ results, isLoading, onRowClick, interval, endDate }: ResultsTableProps) {
    const [sortKey, setSortKey] = useState<SortKey>("prob_profit");
    const [sortAsc, setSortAsc] = useState(false);
    const [backtestPair, setBacktestPair] = useState<PairResult | null>(null);
    const [structurePair, setStructurePair] = useState<PairResult | null>(null);
    const [structure, setStructure] = useState<CreditStructureResult | null>(null);
    const [structureLoading, setStructureLoading] = useState(false);
    const [structureError, setStructureError] = useState("");

    const formatHalfLife = (hl: number, interval: string) => {
        if (interval === "1h") return `${hl.toFixed(2)}h`;
        if (interval === "15m") return `${((hl * 15) / 60).toFixed(2)}h`;
        if (interval === "30m") return `${((hl * 30) / 60).toFixed(2)}h`;
        if (interval === "60m") return `${((hl * 60) / 60).toFixed(2)}h`;
        if (interval === "1d") return `${hl.toFixed(2)}d`;
        return `${hl.toFixed(2)}`;
    };

    const handleSort = (key: SortKey) => {
        if (sortKey === key) {
            setSortAsc(!sortAsc);
        } else {
            setSortKey(key);
            setSortAsc(false);
        }
    };

    const sorted = [...results].sort((a, b) => {
        const av = a[sortKey];
        const bv = b[sortKey];
        if (typeof av === "number" && typeof bv === "number") {
            return sortAsc ? av - bv : bv - av;
        }
        return sortAsc
            ? String(av).localeCompare(String(bv))
            : String(bv).localeCompare(String(av));
    });

    const openBacktest = (pair: PairResult, strategy: BacktestStrategy) => {
        if (!endDate) return;
        const params = new URLSearchParams({
            x: pair.x,
            y: pair.y,
            qty: String(pair.qty),
            direction: pair.direction,
            interval,
            half_life: String(pair.half_life),
            end_date: endDate,
            pair: pair.pair,
            strategy,
        });
        window.open(`/backtest?${params.toString()}`, "_blank", "noopener,noreferrer");
        setBacktestPair(null);
    };

    const openCreditStructure = async (pair: PairResult) => {
        setStructurePair(pair);
        setStructure(null);
        setStructureError("");
        setStructureLoading(true);
        try {
            const result = await getCreditStructure({
                x: pair.x,
                y: pair.y,
                qty: pair.qty,
                direction: pair.direction,
            });
            if (result.status === "failed") throw new Error(result.error || "Unable to build structure.");
            setStructure(result);
        } catch (error) {
            setStructureError(error instanceof Error ? error.message : "Unable to build structure.");
        } finally {
            setStructureLoading(false);
        }
    };

    // If trading days have elapsed since the scan's end date, forward price data
    // exists → show Backtest. Otherwise the end date is current → show Credit Structure.
    const isBacktestScenario = tradingDaysSince(endDate) > 0;

    const isNsePair = (pair: PairResult) =>
        [pair.x, pair.y].every((ticker) =>
            ticker.endsWith(".NS") || ["^NSEI", "^NSEBANK", "NIFTY_FIN_SERVICE.NS"].includes(ticker)
        );

    const columns: { key: SortKey; label: string; width?: string }[] = [
        { key: "combo", label: "Trade" },
        { key: "method", label: "Method", width: "80px" },
        { key: "z_score", label: "Z-Score", width: "80px" },
        { key: "prob_profit", label: "P(Profit)", width: "140px" },
        { key: "half_life", label: "HL", width: "50px" },
        { key: "hurst", label: "Hurst", width: "60px" },
        { key: "exp_return", label: "Exp.Ret", width: "70px" },
        { key: "move_to_mean", label: "Move", width: "70px" },

        { key: "extreme_z_in_hl", label: "Ext.Z?", width: "55px" },
        { key: "same_sector", label: "Sector", width: "55px" },
    ];

    if (isLoading) {
        return (
            <div className="glow-border" style={{
                borderRadius: "16px",
                overflow: "hidden",
                background: "var(--color-bg-secondary)",
            }}>
                <div style={{ padding: "16px 24px", borderBottom: "1px solid var(--color-border)" }}>
                    <div className="skeleton" style={{ width: "100%", height: "20px" }} />
                </div>
                {Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} style={{
                        padding: "14px 24px",
                        display: "flex",
                        gap: "16px",
                        borderBottom: "1px solid rgba(42, 42, 64, 0.3)",
                    }}>
                        {Array.from({ length: 8 }).map((_, j) => (
                            <div key={j} className="skeleton" style={{
                                flex: 1,
                                height: "14px",
                                animationDelay: `${(i * 8 + j) * 0.04}s`,
                            }} />
                        ))}
                    </div>
                ))}
            </div>
        );
    }

    if (results.length === 0) return null;

    return (
        <div className="glow-border" style={{
            borderRadius: "16px",
            overflow: "hidden",
            background: "var(--color-bg-secondary)",
        }}>
            <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12.5px" }}>
                    <thead>
                        <tr style={{ borderBottom: "1px solid var(--color-border)", background: "rgba(26, 26, 46, 0.6)" }}>
                            <th style={{ padding: "12px 12px", textAlign: "center", fontSize: "11px", fontWeight: 600, color: "var(--color-text-muted)", width: "30px" }}>#</th>
                            {columns.map((col) => (
                                <th
                                    key={col.key}
                                    onClick={() => handleSort(col.key)}
                                    style={{
                                        padding: "12px 10px",
                                        textAlign: col.key === "combo" ? "left" : "center",
                                        fontWeight: 600,
                                        fontSize: "10.5px",
                                        textTransform: "uppercase",
                                        letterSpacing: "0.04em",
                                        color: sortKey === col.key ? "var(--color-accent-cyan)" : "var(--color-text-secondary)",
                                        cursor: "pointer",
                                        userSelect: "none",
                                        transition: "color 0.2s",
                                        whiteSpace: "nowrap",
                                        width: col.width,
                                    }}
                                >
                                    {col.label}
                                    {sortKey === col.key && (
                                        <span style={{ marginLeft: "3px" }}>{sortAsc ? "↑" : "↓"}</span>
                                    )}
                                </th>
                            ))}
                            <th style={{ padding: "12px 10px", textAlign: "center", fontSize: "10.5px", fontWeight: 600, color: "var(--color-text-secondary)", width: "86px", textTransform: "uppercase", letterSpacing: "0.04em" }}>
                                Actions
                            </th>
                        </tr>
                    </thead>
                    <tbody>
                        {sorted.map((res, i) => (
                            <tr
                                key={res.pair + i}
                                onClick={() => onRowClick(res)}
                                className="animate-fade-in-up"
                                style={{
                                    borderBottom: "1px solid rgba(42, 42, 64, 0.3)",
                                    cursor: "pointer",
                                    transition: "background 0.2s",
                                    animationDelay: `${i * 0.04}s`,
                                    opacity: 0,
                                }}
                                onMouseEnter={(e) => {
                                    e.currentTarget.style.background = "rgba(99, 102, 241, 0.06)";
                                }}
                                onMouseLeave={(e) => {
                                    e.currentTarget.style.background = "transparent";
                                }}
                            >
                                {/* Row # */}
                                <td style={{ padding: "12px 12px", textAlign: "center", color: "var(--color-text-muted)", fontSize: "11px" }}>
                                    {i + 1}
                                </td>
                                {/* Trade Combo */}
                                <td style={{ padding: "12px 10px", fontSize: "11.5px", lineHeight: 1.3, maxWidth: "320px" }}>
                                    <span style={{ fontWeight: 600 }}>{res.combo}</span>
                                </td>
                                {/* Method */}
                                <td style={{
                                    padding: "12px 10px",
                                    textAlign: "center",
                                    fontSize: "10.5px",
                                    fontWeight: 600,
                                    color: res.method === "Both" ? "var(--color-accent-green)" :
                                        res.method === "Johansen" ? "var(--color-accent-purple)" :
                                            "var(--color-accent-blue)",
                                }}>
                                    {res.method}
                                </td>
                                {/* Z-Score */}
                                <td style={{
                                    padding: "12px 10px",
                                    textAlign: "center",
                                    fontFamily: "var(--font-mono)",
                                    fontWeight: 600,
                                    fontSize: "12px",
                                    color: res.z_score > 0 ? "var(--color-accent-red)" : "var(--color-accent-green)",
                                }}>
                                    {res.z_score > 0 ? "+" : ""}{res.z_score.toFixed(2)}
                                </td>
                                {/* P(Profit) with CI */}
                                <td style={{ padding: "12px 10px", textAlign: "center" }}>
                                    <div style={{ display: "flex", alignItems: "center", gap: "6px", justifyContent: "center" }}>
                                        <div style={{
                                            width: "48px",
                                            height: "5px",
                                            borderRadius: "3px",
                                            background: "rgba(42, 42, 64, 0.5)",
                                            overflow: "hidden",
                                        }}>
                                            <div style={{
                                                width: `${res.prob_profit}%`,
                                                height: "100%",
                                                borderRadius: "3px",
                                                background: res.prob_profit >= 70
                                                    ? "linear-gradient(90deg, var(--color-accent-green), var(--color-accent-cyan))"
                                                    : res.prob_profit >= 50
                                                        ? "linear-gradient(90deg, var(--color-accent-blue), var(--color-accent-cyan))"
                                                        : "linear-gradient(90deg, var(--color-accent-yellow), var(--color-accent-red))",
                                                transition: "width 0.6s ease-out",
                                            }} />
                                        </div>
                                        <span style={{
                                            fontFamily: "var(--font-mono)",
                                            fontWeight: 700,
                                            fontSize: "12px",
                                            color: res.prob_profit >= 70
                                                ? "var(--color-accent-green)"
                                                : res.prob_profit >= 50
                                                    ? "var(--color-accent-blue)"
                                                    : "var(--color-accent-yellow)",
                                        }}>
                                            {res.prob_profit.toFixed(2)}%
                                        </span>
                                    </div>
                                    <div style={{ fontSize: "9.5px", color: "var(--color-text-muted)", marginTop: "2px" }}>
                                        {res.prob_profit_low.toFixed(2)}–{res.prob_profit_high.toFixed(2)}%
                                    </div>
                                </td>
                                {/* Half-Life */}
                                <td style={{ padding: "12px 10px", textAlign: "center", color: "var(--color-text-secondary)", fontSize: "12px" }}>
                                    {formatHalfLife(res.half_life, interval)}
                                </td>
                                {/* Hurst */}
                                <td style={{
                                    padding: "12px 10px",
                                    textAlign: "center",
                                    fontFamily: "var(--font-mono)",
                                    fontSize: "12px",
                                    color: res.hurst < 0.35 ? "var(--color-accent-green)" :
                                        res.hurst < 0.45 ? "var(--color-accent-cyan)" :
                                            "var(--color-accent-yellow)",
                                }}>
                                    {res.hurst.toFixed(2)}
                                </td>
                                {/* Exp Return */}
                                <td style={{
                                    padding: "12px 10px",
                                    textAlign: "center",
                                    fontWeight: 600,
                                    fontFamily: "var(--font-mono)",
                                    fontSize: "12px",
                                    color: "var(--color-accent-yellow)",
                                }}>
                                    {res.exp_return.toFixed(2)}%
                                </td>
                                {/* Move to Mean */}
                                <td style={{
                                    padding: "12px 10px",
                                    textAlign: "center",
                                    fontFamily: "var(--font-mono)",
                                    fontSize: "11.5px",
                                    color: "var(--color-text-secondary)",
                                }}>
                                    {res.move_to_mean.toFixed(2)}
                                </td>

                                {/* Extreme Z in HL */}
                                <td style={{
                                    padding: "12px 10px",
                                    textAlign: "center",
                                    fontSize: "11px",
                                    fontWeight: 600,
                                    color: res.extreme_z_in_hl === "Yes" ? "var(--color-accent-red)" : "var(--color-text-muted)",
                                }}>
                                    {res.extreme_z_in_hl}
                                </td>
                                {/* Same Sector */}
                                <td style={{
                                    padding: "12px 10px",
                                    textAlign: "center",
                                    fontSize: "11px",
                                    color: res.same_sector === "Yes" ? "var(--color-accent-green)" : "var(--color-text-muted)",
                                }}>
                                    {res.same_sector}
                                </td>
                                <td style={{ padding: "12px 10px", textAlign: "center" }}>
                                    <div style={{ display: "grid", gap: "6px" }}>
                                        {isBacktestScenario ? (
                                            <button
                                                onClick={(event) => {
                                                    event.stopPropagation();
                                                    setBacktestPair(res);
                                                }}
                                                title="Open forward half-life backtest"
                                                style={{
                                                    padding: "7px 10px", borderRadius: "8px", border: "1px solid var(--color-border)",
                                                    background: "rgba(99, 102, 241, 0.16)",
                                                    color: "var(--color-accent-cyan)",
                                                    fontSize: "11px", fontWeight: 700, cursor: "pointer",
                                                }}
                                            >
                                                Backtest
                                            </button>
                                        ) : (
                                            <button
                                                onClick={(event) => {
                                                    event.stopPropagation();
                                                    openCreditStructure(res);
                                                }}
                                                disabled={interval !== "1d" || !isNsePair(res)}
                                                title={interval !== "1d" ? "Credit structures require daily scans" : "Show current NSE credit spread structure"}
                                                style={{
                                                    padding: "7px 10px", borderRadius: "8px", border: "1px solid var(--color-border)",
                                                    background: interval === "1d" && isNsePair(res) ? "rgba(52,211,153,0.12)" : "rgba(42,42,64,0.35)",
                                                    color: interval === "1d" && isNsePair(res) ? "var(--color-accent-green)" : "var(--color-text-muted)",
                                                    fontSize: "10px", fontWeight: 700,
                                                    cursor: interval === "1d" && isNsePair(res) ? "pointer" : "not-allowed",
                                                    whiteSpace: "nowrap",
                                                }}
                                            >
                                                Credit Structure
                                            </button>
                                        )}
                                    </div>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            {backtestPair && (
                <div
                    onClick={() => setBacktestPair(null)}
                    style={{
                        position: "fixed", inset: 0, zIndex: 1100, background: "rgba(0,0,0,0.72)",
                        backdropFilter: "blur(8px)", display: "flex", alignItems: "center",
                        justifyContent: "center", padding: "20px",
                    }}
                >
                    <div
                        onClick={(event) => event.stopPropagation()}
                        className="glow-border"
                        style={{
                            width: "100%", maxWidth: "520px", padding: "24px", borderRadius: "16px",
                            background: "var(--color-bg-secondary)",
                        }}
                    >
                        <h2 style={{ fontSize: "18px", fontWeight: 800 }}>Choose backtest structure</h2>
                        <p style={{ marginTop: "5px", color: "var(--color-text-muted)", fontSize: "12px" }}>
                            {backtestPair.pair} • {interval === "1d" ? "Daily data" : "Intraday scan"}
                        </p>
                        <div style={{ display: "grid", gap: "10px", marginTop: "18px" }}>
                            {([
                                ["equity", "Equities only", "Original cash-equity pair"],
                                ["futures", "Futures only", "Nearest eligible monthly futures"],
                                ["futures_options", "Futures + option buy", "Future with a 2% OTM protective option"],
                                ["credit_spreads", "Credit spreads", "Bull put / bear call spreads with three-strike hedges"],
                            ] as [BacktestStrategy, string, string][]).map(([value, label, detail]) => {
                                const disabled = value !== "equity" && interval !== "1d";
                                return (
                                    <button
                                        key={value}
                                        disabled={disabled}
                                        onClick={() => openBacktest(backtestPair, value)}
                                        style={{
                                            padding: "13px 15px", borderRadius: "10px", textAlign: "left",
                                            border: "1px solid var(--color-border)",
                                            background: disabled ? "rgba(42,42,64,0.25)" : "rgba(99,102,241,0.10)",
                                            color: disabled ? "var(--color-text-muted)" : "var(--color-text-primary)",
                                            cursor: disabled ? "not-allowed" : "pointer",
                                        }}
                                    >
                                        <div style={{ fontSize: "13px", fontWeight: 700 }}>{label}</div>
                                        <div style={{ fontSize: "11px", color: "var(--color-text-muted)", marginTop: "3px" }}>
                                            {disabled ? "Unavailable for intraday scans" : detail}
                                        </div>
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                </div>
            )}
            {structurePair && (
                <div
                    onClick={() => setStructurePair(null)}
                    style={{
                        position: "fixed", inset: 0, zIndex: 1150, background: "rgba(0,0,0,0.72)",
                        backdropFilter: "blur(8px)", display: "flex", alignItems: "center",
                        justifyContent: "center", padding: "20px",
                    }}
                >
                    <div
                        onClick={(event) => event.stopPropagation()}
                        className="glow-border"
                        style={{ width: "100%", maxWidth: "680px", padding: "24px", borderRadius: "16px", background: "var(--color-bg-secondary)" }}
                    >
                        <div style={{ display: "flex", justifyContent: "space-between", gap: "16px" }}>
                            <div>
                                <h2 style={{ fontSize: "18px", fontWeight: 800 }}>Current credit spread structure</h2>
                                <p style={{ marginTop: "5px", color: "var(--color-text-muted)", fontSize: "12px" }}>
                                    {structurePair.pair}{structure?.as_of ? ` • NSE snapshot ${structure.as_of}` : ""}
                                </p>
                            </div>
                            <button onClick={() => setStructurePair(null)} style={{ border: 0, background: "transparent", color: "var(--color-text-secondary)", cursor: "pointer", fontSize: "18px" }}>✕</button>
                        </div>

                        {structureLoading && <p style={{ marginTop: "22px", color: "var(--color-text-secondary)" }}>Loading current futures lots and option chain…</p>}
                        {structureError && <p style={{ marginTop: "22px", color: "var(--color-accent-red)" }}>{structureError}</p>}
                        {structure?.legs && (
                            <>
                                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "10px", marginTop: "18px" }}>
                                    {[
                                        ["Target ratio", structure.qty?.toFixed(2)],
                                        ["Whole-lot ratio", structure.actual_ratio?.toFixed(4)],
                                        ["Lots (X / Y)", `${structure.x_lots} / ${structure.y_lots}`],
                                    ].map(([label, value]) => (
                                        <div key={label} style={{ padding: "11px", borderRadius: "9px", border: "1px solid var(--color-border)", background: "rgba(10,10,15,0.5)" }}>
                                            <div style={{ fontSize: "9px", color: "var(--color-text-muted)", textTransform: "uppercase" }}>{label}</div>
                                            <div style={{ marginTop: "4px", fontFamily: "var(--font-mono)", fontWeight: 700 }}>{value}</div>
                                        </div>
                                    ))}
                                </div>
                                {structure.margin && (
                                    <div style={{ marginTop: "12px", padding: "13px", borderRadius: "9px", border: "1px solid var(--color-border)", background: "rgba(52,211,153,0.07)" }}>
                                        <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
                                            <div>
                                                <div style={{ fontSize: "9px", color: "var(--color-text-muted)", textTransform: "uppercase" }}>Estimated margin required</div>
                                                <div style={{ marginTop: "4px", fontFamily: "var(--font-mono)", fontWeight: 800, color: "var(--color-accent-green)" }}>₹{structure.margin.estimated_margin.toFixed(2)}</div>
                                            </div>
                                            <div>
                                                <div style={{ fontSize: "9px", color: "var(--color-text-muted)", textTransform: "uppercase" }}>Suggested funds (+{structure.margin.buffer_pct}%)</div>
                                                <div style={{ marginTop: "4px", fontFamily: "var(--font-mono)", fontWeight: 800 }}>₹{structure.margin.suggested_funds.toFixed(2)}</div>
                                            </div>
                                        </div>
                                        <div style={{ marginTop: "8px", color: "var(--color-text-muted)", fontSize: "10px", lineHeight: 1.6 }}>
                                            SPAN estimate ₹{structure.margin.span_estimate.toFixed(2)} • ELM ₹{structure.margin.elm.toFixed(2)} • Premium debit ₹{structure.margin.premium_debit.toFixed(2)}
                                            <br />{structure.margin.method}; indicative only, not an exact NSE or broker margin statement.
                                        </div>
                                    </div>
                                )}
                                <div style={{ display: "grid", gap: "9px", marginTop: "18px" }}>
                                    {structure.legs.map((leg, index) => (
                                        <div key={`${leg.asset}-${leg.side}-${index}`} style={{ padding: "12px", borderRadius: "9px", border: "1px solid var(--color-border)", fontFamily: "var(--font-mono)", fontSize: "12px" }}>
                                            <strong style={{ color: leg.side === "BUY" ? "var(--color-accent-green)" : "var(--color-accent-red)" }}>{leg.side}</strong>
                                            {" "}{leg.lots} lot{leg.lots === 1 ? "" : "s"} × {leg.lot_size} {leg.symbol} {leg.expiry} {leg.strike} {leg.instrument}
                                            <span style={{ color: "var(--color-text-muted)" }}> • spot ₹{leg.spot?.toFixed(2)}</span>
                                            {leg.price !== undefined && <span style={{ color: "var(--color-text-muted)" }}> • option ₹{leg.price.toFixed(2)}</span>}
                                        </div>
                                    ))}
                                </div>
                                {structure.note && <p style={{ marginTop: "14px", color: "var(--color-text-muted)", fontSize: "11px" }}>{structure.note}</p>}
                            </>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
