"use client";

import { useEffect, useRef } from "react";
import { createChart, type IChartApi, LineSeries, type UTCTimestamp } from "lightweight-charts";
import { type PairResult } from "@/lib/api";

interface PairDetailModalProps {
    pair: PairResult | null;
    onClose: () => void;
}

export default function PairDetailModal({ pair, onClose }: PairDetailModalProps) {
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);

    useEffect(() => {
        if (!pair || !chartContainerRef.current) return;

        const chart = createChart(chartContainerRef.current, {
            width: chartContainerRef.current.clientWidth,
            height: 280,
            layout: {
                background: { color: "transparent" },
                textColor: "#a1a1aa",
                fontSize: 11,
                fontFamily: "'Inter', sans-serif",
            },
            grid: {
                vertLines: { color: "rgba(42, 42, 64, 0.3)" },
                horzLines: { color: "rgba(42, 42, 64, 0.3)" },
            },
            crosshair: {
                vertLine: { color: "rgba(99, 102, 241, 0.4)", width: 1, style: 2 },
                horzLine: { color: "rgba(99, 102, 241, 0.4)", width: 1, style: 2 },
            },
            rightPriceScale: { borderColor: "rgba(42, 42, 64, 0.5)" },
            timeScale: { borderColor: "rgba(42, 42, 64, 0.5)" },
        });
        chartRef.current = chart;

        const data = pair.historical_z_scores.map(item => ({ time: item.time as UTCTimestamp, value: item.value }));
        const upperBand = pair.historical_z_scores.map(item => ({ time: item.time as UTCTimestamp, value: 2 }));
        const lowerBand = pair.historical_z_scores.map(item => ({ time: item.time as UTCTimestamp, value: -2 }));
        const meanLine = pair.historical_z_scores.map(item => ({ time: item.time as UTCTimestamp, value: 0 }));

        chart.addSeries(LineSeries, { color: "#6366f1", lineWidth: 2 }).setData(data);
        chart.addSeries(LineSeries, { color: "#f87171", lineWidth: 1, lineStyle: 2 }).setData(upperBand);
        chart.addSeries(LineSeries, { color: "#34d399", lineWidth: 1, lineStyle: 2 }).setData(lowerBand);
        chart.addSeries(LineSeries, { color: "#71717a", lineWidth: 1, lineStyle: 2 }).setData(meanLine);
        chart.timeScale().fitContent();

        const handleResize = () => {
            if (chartContainerRef.current) chart.applyOptions({ width: chartContainerRef.current.clientWidth });
        };
        window.addEventListener("resize", handleResize);
        return () => { window.removeEventListener("resize", handleResize); chart.remove(); };
    }, [pair]);

    if (!pair) return null;

    const stats = [
        { label: "Z-Score", value: `${pair.z_score > 0 ? "+" : ""}${pair.z_score}`, color: pair.z_score > 0 ? "var(--color-accent-red)" : "var(--color-accent-green)" },
        { label: "P(Profit)", value: `${pair.prob_profit}%`, sub: `${pair.prob_profit_low}–${pair.prob_profit_high}%`, color: "var(--color-accent-blue)" },
        { label: "Half-Life", value: `${pair.half_life}d`, color: "var(--color-text-primary)" },
        { label: "Hurst", value: pair.hurst.toFixed(2), color: pair.hurst < 0.35 ? "var(--color-accent-green)" : "var(--color-accent-cyan)" },
        { label: "Exp. Return", value: `${pair.exp_return}%`, color: "var(--color-accent-yellow)" },
        { label: "Move to Mean", value: `${pair.move_to_mean}`, color: "var(--color-text-secondary)" },
        { label: "Unit Price", value: `₹${pair.unit_price}`, color: "var(--color-text-secondary)" },
        { label: "ρ Price", value: `${pair.price_corr}`, color: pair.price_corr > 0.7 ? "var(--color-accent-green)" : "var(--color-text-secondary)" },
    ];

    return (
        <div onClick={onClose} style={{
            position: "fixed", inset: 0, background: "rgba(0, 0, 0, 0.7)", backdropFilter: "blur(8px)",
            display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000, padding: "20px",
        }}>
            <div onClick={(e) => e.stopPropagation()} className="glow-border animate-fade-in-up" style={{
                width: "100%", maxWidth: "800px", borderRadius: "20px",
                background: "linear-gradient(180deg, var(--color-bg-card), var(--color-bg-secondary))", padding: "28px", position: "relative",
            }}>
                {/* Header */}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "20px" }}>
                    <div>
                        <h2 style={{ fontSize: "20px", fontWeight: 700, fontFamily: "var(--font-mono)", letterSpacing: "-0.02em" }}>{pair.pair}</h2>
                        <p style={{ fontSize: "12px", color: "var(--color-text-muted)", marginTop: "4px" }}>
                            {pair.method} • {pair.half_life}-day half-life • {pair.same_sector === "Yes" ? "Same Sector" : "Cross Sector"}
                        </p>
                        <p style={{ fontSize: "11.5px", color: "var(--color-text-secondary)", marginTop: "6px", lineHeight: 1.4, maxWidth: "600px" }}>
                            {pair.combo}
                        </p>
                    </div>
                    <button onClick={onClose} style={{
                        width: "32px", height: "32px", borderRadius: "8px", border: "1px solid var(--color-border)",
                        background: "transparent", color: "var(--color-text-secondary)", fontSize: "16px", cursor: "pointer",
                        display: "flex", alignItems: "center", justifyContent: "center",
                    }}>✕</button>
                </div>

                {/* Stats Grid */}
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "10px", marginBottom: "20px" }}>
                    {stats.map((stat) => (
                        <div key={stat.label} style={{
                            padding: "12px", borderRadius: "10px", background: "rgba(10, 10, 15, 0.5)",
                            border: "1px solid var(--color-border)", textAlign: "center",
                        }}>
                            <div style={{ fontSize: "10px", color: "var(--color-text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "4px" }}>{stat.label}</div>
                            <div style={{ fontSize: "17px", fontWeight: 700, fontFamily: "var(--font-mono)", color: stat.color }}>{stat.value}</div>
                            {"sub" in stat && stat.sub && <div style={{ fontSize: "9.5px", color: "var(--color-text-muted)", marginTop: "2px" }}>{stat.sub}</div>}
                        </div>
                    ))}
                </div>

                {/* Extreme Z info */}
                {pair.extreme_z_detail && (
                    <div style={{
                        padding: "10px 14px", borderRadius: "8px", marginBottom: "16px",
                        background: "rgba(42, 42, 64, 0.3)", border: "1px solid var(--color-border)",
                        fontSize: "12px", color: "var(--color-text-secondary)", display: "flex", gap: "16px",
                    }}>
                        <span>Extreme Z in HL: <strong style={{ color: pair.extreme_z_in_hl === "Yes" ? "var(--color-accent-red)" : "var(--color-text-primary)" }}>{pair.extreme_z_in_hl}</strong> ({pair.extreme_z_detail})</span>
                        {pair.profitable_since_extreme !== "N/A" && (
                            <span>PnL since: <strong style={{ color: pair.pnl_since_extreme > 0 ? "var(--color-accent-green)" : "var(--color-accent-red)" }}>{pair.pnl_since_extreme}</strong></span>
                        )}
                    </div>
                )}

                {/* Chart */}
                <div style={{ borderRadius: "10px", overflow: "hidden", border: "1px solid var(--color-border)", background: "rgba(10, 10, 15, 0.5)", padding: "10px" }}>
                    <div style={{ display: "flex", gap: "14px", marginBottom: "8px", fontSize: "10px", color: "var(--color-text-muted)" }}>
                        <span><span style={{ width: "10px", height: "2px", background: "#6366f1", display: "inline-block", verticalAlign: "middle", marginRight: "4px" }} />Z-Score</span>
                        <span><span style={{ width: "10px", height: "2px", background: "#f87171", display: "inline-block", verticalAlign: "middle", marginRight: "4px" }} />+2σ</span>
                        <span><span style={{ width: "10px", height: "2px", background: "#34d399", display: "inline-block", verticalAlign: "middle", marginRight: "4px" }} />-2σ</span>
                    </div>
                    <div ref={chartContainerRef} />
                </div>

                {/* Signal */}
                <div style={{
                    marginTop: "14px", padding: "10px 14px", borderRadius: "8px",
                    background: pair.z_score > 0 ? "rgba(248, 113, 113, 0.08)" : "rgba(52, 211, 153, 0.08)",
                    border: `1px solid ${pair.z_score > 0 ? "rgba(248,113,113,0.2)" : "rgba(52,211,153,0.2)"}`,
                    fontSize: "12px", color: pair.z_score > 0 ? "var(--color-accent-red)" : "var(--color-accent-green)",
                    textAlign: "center", fontWeight: 600,
                }}>
                    Signal: {pair.z_score > 0 ? "Sell" : "Buy"} {pair.pair.split("/")[0]} — {pair.z_score > 0 ? "Buy" : "Sell"} {pair.pair.split("/")[1]}
                </div>
            </div>
        </div>
    );
}
