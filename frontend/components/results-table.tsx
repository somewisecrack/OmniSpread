"use client";

import { type PairResult } from "@/lib/api";
import { useState } from "react";

interface ResultsTableProps {
    results: PairResult[];
    isLoading: boolean;
    onRowClick: (pair: PairResult) => void;
    interval: string;
}

type SortKey = keyof PairResult;

export default function ResultsTable({ results, isLoading, onRowClick, interval }: ResultsTableProps) {
    const [sortKey, setSortKey] = useState<SortKey>("prob_profit");
    const [sortAsc, setSortAsc] = useState(false);

    const formatHalfLife = (hl: number, interval: string) => {
        if (interval === "1h") return `${hl}h`;
        if (interval === "15m") return `${(hl * 15) / 60}h`;
        if (interval === "30m") return `${(hl * 30) / 60}h`;
        if (interval === "60m") return `${(hl * 60) / 60}h`;
        if (interval === "1d") return `${hl}d`;
        return `${hl}`;
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
                                    {res.z_score > 0 ? "+" : ""}{res.z_score}
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
                                            {res.prob_profit}%
                                        </span>
                                    </div>
                                    <div style={{ fontSize: "9.5px", color: "var(--color-text-muted)", marginTop: "2px" }}>
                                        {res.prob_profit_low}–{res.prob_profit_high}%
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
                                    {res.exp_return}%
                                </td>
                                {/* Move to Mean */}
                                <td style={{
                                    padding: "12px 10px",
                                    textAlign: "center",
                                    fontFamily: "var(--font-mono)",
                                    fontSize: "11.5px",
                                    color: "var(--color-text-secondary)",
                                }}>
                                    {res.move_to_mean}
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
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
