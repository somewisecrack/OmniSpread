"use client";

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { createChart, type IChartApi, LineSeries, type UTCTimestamp } from "lightweight-charts";
import { runBacktest, type BacktestResult } from "@/lib/api";

function formatTime(timestamp?: number) {
    if (!timestamp) return "N/A";
    return new Date(timestamp * 1000).toLocaleString();
}

function ChartPanel({ title, data, color, valueSuffix = "", headerDetail }: {
    title: string;
    data: { time: UTCTimestamp; value: number }[];
    color: string;
    valueSuffix?: string;
    headerDetail?: string;
}) {
    const ref = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);

    useEffect(() => {
        if (!ref.current) return;
        const chart = createChart(ref.current, {
            width: ref.current.clientWidth,
            height: 300,
            layout: {
                background: { color: "transparent" },
                textColor: "#a1a1aa",
                fontSize: 11,
                fontFamily: "'Inter', sans-serif",
            },
            grid: {
                vertLines: { color: "rgba(42, 42, 64, 0.35)" },
                horzLines: { color: "rgba(42, 42, 64, 0.35)" },
            },
            rightPriceScale: { borderColor: "rgba(42, 42, 64, 0.5)" },
            timeScale: { borderColor: "rgba(42, 42, 64, 0.5)" },
        });
        chartRef.current = chart;
        chart.addSeries(LineSeries, {
            color,
            lineWidth: 2,
            priceFormat: { type: "price", precision: valueSuffix ? 2 : 4, minMove: valueSuffix ? 0.01 : 0.0001 },
        }).setData(data);
        chart.timeScale().fitContent();

        const handleResize = () => {
            if (ref.current) chart.applyOptions({ width: ref.current.clientWidth });
        };
        window.addEventListener("resize", handleResize);
        return () => {
            window.removeEventListener("resize", handleResize);
            chart.remove();
            chartRef.current = null;
        };
    }, [data, color, valueSuffix]);

    return (
        <section className="glow-border" style={{ borderRadius: "14px", background: "var(--color-bg-secondary)", padding: "18px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px" }}>
                <div>
                    <h2 style={{ fontSize: "15px", fontWeight: 700 }}>{title}</h2>
                    {headerDetail && (
                        <p style={{ color: "var(--color-text-muted)", fontSize: "11px", marginTop: "4px" }}>
                            {headerDetail}
                        </p>
                    )}
                </div>
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--color-text-secondary)", fontSize: "12px" }}>
                    {data.length > 0 ? `${data[data.length - 1].value.toFixed(valueSuffix ? 2 : 4)}${valueSuffix}` : "N/A"}
                </span>
            </div>
            <div ref={ref} />
        </section>
    );
}

function BacktestContent() {
    const params = useSearchParams();
    const [result, setResult] = useState<BacktestResult | null>(null);
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(true);

    const request = useMemo(() => ({
        x: params.get("x") || "",
        y: params.get("y") || "",
        qty: Number(params.get("qty") || "0"),
        direction: params.get("direction") || "",
        interval: params.get("interval") || "1d",
        half_life: Number(params.get("half_life") || "0"),
        end_date: params.get("end_date") || "",
    }), [params]);

    useEffect(() => {
        const load = async () => {
            setLoading(true);
            setError("");
            try {
                if (!request.x || !request.y || !request.qty || !request.direction || !request.half_life || !request.end_date) {
                    throw new Error("Missing backtest parameters.");
                }
                const data = await runBacktest(request);
                if (data.status === "failed") {
                    throw new Error(data.error || "Backtest failed.");
                }
                setResult(data);
            } catch (err) {
                setError(err instanceof Error ? err.message : "Backtest failed.");
            } finally {
                setLoading(false);
            }
        };
        load();
    }, [request]);

    const spreadData = (result?.points || []).map((point) => ({ time: point.time as UTCTimestamp, value: point.spread }));
    const pnlData = (result?.points || []).map((point) => ({ time: point.time as UTCTimestamp, value: point.pnl_pct }));
    const xCloseData = (result?.points || []).map((point) => ({ time: point.time as UTCTimestamp, value: point.x }));
    const yCloseData = (result?.points || []).map((point) => ({ time: point.time as UTCTimestamp, value: point.y }));
    const pnl = result?.final_pnl_pct ?? 0;
    const maxProfit = result?.max_profit_pct ?? 0;

    return (
        <main style={{ maxWidth: "1120px", margin: "0 auto", padding: "36px 24px 72px" }}>
            <header style={{ marginBottom: "24px" }}>
                <p style={{ color: "var(--color-text-muted)", fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: "8px" }}>
                    Forward half-life backtest
                </p>
                <h1 style={{ fontSize: "30px", fontWeight: 800 }}>
                    <span className="gradient-text">{params.get("pair") || result?.pair || "Pair"}</span>
                </h1>
            </header>

            {loading && (
                <div className="glow-border" style={{ borderRadius: "14px", padding: "24px", color: "var(--color-text-secondary)" }}>
                    Loading forward prices and calculating realized PnL...
                </div>
            )}

            {error && (
                <div className="glow-border" style={{ borderRadius: "14px", padding: "24px", color: "var(--color-accent-red)" }}>
                    {error}
                </div>
            )}

            {result && !loading && !error && (
                <>
                    <section className="glow-border backtest-summary-grid" style={{
                        display: "grid",
                        gap: "12px",
                        borderRadius: "14px",
                        padding: "16px",
                        background: "var(--color-bg-secondary)",
                        marginBottom: "18px",
                    }}>
                        {[
                            { label: "Actual PnL", value: `${pnl > 0 ? "+" : ""}${pnl.toFixed(2)}%`, color: pnl >= 0 ? "var(--color-accent-green)" : "var(--color-accent-red)" },
                            { label: "Max Profit", value: `${maxProfit > 0 ? "+" : ""}${maxProfit.toFixed(2)}%`, color: maxProfit >= 0 ? "var(--color-accent-green)" : "var(--color-accent-red)" },
                            { label: "Half-Life", value: `${result.half_life} bars`, color: "var(--color-text-primary)" },
                            { label: "Interval", value: result.interval || request.interval, color: "var(--color-accent-cyan)" },
                        ].map((item) => (
                            <div key={item.label} style={{ padding: "12px", borderRadius: "10px", background: "rgba(10,10,15,0.5)", border: "1px solid var(--color-border)" }}>
                                <div style={{ fontSize: "10px", color: "var(--color-text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "6px" }}>
                                    {item.label}
                                </div>
                                <div style={{ fontSize: "20px", fontWeight: 800, fontFamily: "var(--font-mono)", color: item.color }}>
                                    {item.value}
                                </div>
                            </div>
                        ))}
                    </section>

                    <div style={{ color: "var(--color-text-muted)", fontSize: "12px", marginBottom: "18px" }}>
                        Entry: {formatTime(result.entry_time)} • Last bar: {formatTime(result.exit_time)}
                    </div>

                    <div style={{ display: "grid", gap: "18px" }}>
                        <div className="backtest-close-grid" style={{ display: "grid", gap: "18px" }}>
                            <ChartPanel title={`${result.x || request.x} Close`} data={xCloseData} color="#22d3ee" />
                            <ChartPanel title={`${result.y || request.y} Close`} data={yCloseData} color="#a78bfa" />
                        </div>
                        <ChartPanel title="Spread" data={spreadData} color="#6366f1" />
                        <ChartPanel
                            title="Actual PnL"
                            data={pnlData}
                            color={pnl >= 0 ? "#34d399" : "#f87171"}
                            valueSuffix="%"
                            headerDetail={`Max profit during period: ${maxProfit > 0 ? "+" : ""}${maxProfit.toFixed(2)}%`}
                        />
                    </div>

                    {result.note && (
                        <p style={{ color: "var(--color-text-muted)", fontSize: "12px", marginTop: "16px" }}>
                            {result.note}
                        </p>
                    )}
                </>
            )}
        </main>
    );
}

export default function BacktestPage() {
    return (
        <Suspense fallback={null}>
            <BacktestContent />
        </Suspense>
    );
}
