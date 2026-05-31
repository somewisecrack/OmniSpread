"use client";

import { useState, useEffect } from "react";
import ReactDatePicker from "react-datepicker";
import "react-datepicker/dist/react-datepicker.css";
import { getPresets, type Presets } from "@/lib/api";

interface ScanFormProps {
    onScan: (tickers: string[], period: string, interval?: string, startDate?: string, endDate?: string) => void;
    onReset: () => void;
    isScanning: boolean;
}

const PERIODS = [
    { value: "60d", label: "60 Days" },
    { value: "6mo", label: "6 Months" },
    { value: "1y", label: "1 Year" },
    { value: "2y", label: "2 Years" },
    { value: "3y", label: "3 Years" },
    { value: "5y", label: "5 Years" },
    { value: "custom", label: "Custom" },
];

const MS_PER_DAY = 24 * 60 * 60 * 1000;
const INTRADAY_OPTIONS = ["15m", "30m"];
const SWING_OPTIONS = ["1h", "1d"];
const DAILY_OPTIONS = ["1d"];

const normalizeDate = (date: Date) =>
    new Date(date.getFullYear(), date.getMonth(), date.getDate());

const getDateSpanDays = (start: Date | null, end: Date | null) => {
    if (!start || !end) return null;
    const startTime = normalizeDate(start).getTime();
    const endTime = normalizeDate(end).getTime();
    if (endTime < startTime) return null;
    return Math.floor((endTime - startTime) / MS_PER_DAY) + 1;
};

const getIntervalOptions = (periodValue: string, start: Date | null, end: Date | null) => {
    if (periodValue === "60d") return INTRADAY_OPTIONS;
    if (periodValue === "6mo") return SWING_OPTIONS;
    if (periodValue === "1y") return DAILY_OPTIONS;
    if (periodValue === "custom") {
        const spanDays = getDateSpanDays(start, end);
        if (spanDays === null) return DAILY_OPTIONS;
        if (spanDays <= 60) return INTRADAY_OPTIONS;
        if (spanDays < 365) return SWING_OPTIONS;
        return DAILY_OPTIONS;
    }
    return DAILY_OPTIONS;
};

const getDefaultCustomDates = () => {
    const end = normalizeDate(new Date());
    const start = new Date(end);
    start.setDate(end.getDate() - 29);
    return { start, end };
};

const parseTickers = (input: string) =>
    input
        .split(/[,\s]+/)
        .map((t) => t.trim().toUpperCase())
        .filter(Boolean);

export default function ScanForm({ onScan, onReset, isScanning }: ScanFormProps) {
    const [tickerInput, setTickerInput] = useState("");
    const [period, setPeriod] = useState("3y");
    const [interval, setIntervalVal] = useState("1d");
    const [startDate, setStartDate] = useState<Date | null>(null);
    const [endDate, setEndDate] = useState<Date | null>(null);
    const [presets, setPresets] = useState<Presets>({});
    const [activePreset, setActivePreset] = useState<string | null>(null);
    const [presetError, setPresetError] = useState("");

    useEffect(() => {
        getPresets()
            .then((loadedPresets) => {
                setPresets(loadedPresets);
                if (loadedPresets.nifty_50?.length) {
                    setTickerInput((current) => current.trim() ? current : loadedPresets.nifty_50.join(", "));
                    setActivePreset((current) => current ?? "nifty_50");
                }
                setPresetError("");
            })
            .catch((error) => {
                setPresetError(error instanceof Error ? error.message : "Unable to load presets");
            });
    }, []);

    const handlePeriodChange = (nextPeriod: string) => {
        let nextStartDate = startDate;
        let nextEndDate = endDate;
        if (nextPeriod === "custom" && (!nextStartDate || !nextEndDate)) {
            const defaults = getDefaultCustomDates();
            nextStartDate = defaults.start;
            nextEndDate = defaults.end;
            setStartDate(nextStartDate);
            setEndDate(nextEndDate);
        }

        setPeriod(nextPeriod);
        const nextOptions = getIntervalOptions(nextPeriod, nextStartDate, nextEndDate);
        setIntervalVal((current) => (nextOptions.includes(current) ? current : nextOptions[0]));
    };

    const handleCustomDateChange = (field: "start" | "end", date: Date | null) => {
        const nextStartDate = field === "start" ? date : startDate;
        const nextEndDate = field === "end" ? date : endDate;
        if (field === "start") {
            setStartDate(date);
        } else {
            setEndDate(date);
        }

        if (period === "custom") {
            const nextOptions = getIntervalOptions(period, nextStartDate, nextEndDate);
            setIntervalVal((current) => (nextOptions.includes(current) ? current : nextOptions[0]));
        }
    };


    const handlePreset = (key: string) => {
        const tickers = presets[key];
        if (tickers) {
            setTickerInput(tickers.join(", "));
            setActivePreset(key);
        }
    };

    const handleReset = () => {
        setTickerInput("");
        setPeriod("3y");
        setIntervalVal("1d");
        setStartDate(null);
        setEndDate(null);
        setActivePreset(null);
        onReset();
    };

    const handleScan = () => {
        const tickers = parseTickers(tickerInput);
        if (tickers.length >= 2) {
            const startStr = startDate ? startDate.toISOString().split("T")[0] : undefined;
            const endStr = endDate ? endDate.toISOString().split("T")[0] : undefined;

            let finalInterval = interval;
            if (period !== "60d" && period !== "6mo" && period !== "1y" && period !== "custom") {
                finalInterval = "1d";
            }
            onScan(tickers, period, finalInterval, startStr, endStr);
        }
    };

    const formatPresetName = (key: string) =>
        key
            .split("_")
            .map((w) => {
                if (w.toLowerCase() === "fno") return "F&O";
                return w.charAt(0).toUpperCase() + w.slice(1);
            })
            .join(" ");

    const tickerCount = parseTickers(tickerInput).length;
    const canScan = !isScanning && tickerCount >= 2;

    return (
        <div className="glow-border" style={{
            borderRadius: "16px",
            padding: "28px",
            background: "linear-gradient(135deg, rgba(26, 26, 46, 0.8), rgba(18, 18, 26, 0.9))",
            backdropFilter: "blur(10px)",
        }}>
            {/* Preset Buttons */}
            <div style={{ marginBottom: "20px" }}>
                <label style={{
                    display: "block",
                    fontSize: "12px",
                    fontWeight: 600,
                    color: "var(--color-text-secondary)",
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                    marginBottom: "10px",
                }}>
                    Presets
                </label>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
                    {Object.keys(presets).map((key) => (
                        <button
                            key={key}
                            onClick={() => handlePreset(key)}
                            style={{
                                padding: "6px 14px",
                                borderRadius: "8px",
                                fontSize: "13px",
                                fontWeight: 500,
                                cursor: "pointer",
                                transition: "all 0.2s",
                                border: activePreset === key
                                    ? "1px solid var(--color-accent-blue)"
                                    : "1px solid var(--color-border)",
                                background: activePreset === key
                                    ? "rgba(99, 102, 241, 0.15)"
                                    : "rgba(42, 42, 64, 0.5)",
                                color: activePreset === key
                                    ? "var(--color-accent-cyan)"
                                    : "var(--color-text-secondary)",
                            }}
                        >
                            {formatPresetName(key)}
                        </button>
                    ))}
                </div>
                {presetError && (
                    <p style={{
                        color: "var(--color-accent-red)",
                        fontSize: "12px",
                        marginTop: "8px",
                    }}>
                        Presets unavailable: {presetError}
                    </p>
                )}
            </div>

            {/* Ticker Input */}
            <div style={{ marginBottom: "20px" }}>
                <label style={{
                    display: "block",
                    fontSize: "12px",
                    fontWeight: 600,
                    color: "var(--color-text-secondary)",
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                    marginBottom: "8px",
                }}>
                    Tickers (comma-separated)
                </label>
                <textarea
                    value={tickerInput}
                    onChange={(e) => {
                        setTickerInput(e.target.value);
                        setActivePreset(null);
                    }}
                    placeholder="AAPL, MSFT, GOOGL, AMZN, META, NVDA..."
                    rows={2}
                    style={{
                        width: "100%",
                        padding: "12px 16px",
                        borderRadius: "10px",
                        border: "1px solid var(--color-border)",
                        background: "rgba(10, 10, 15, 0.6)",
                        color: "var(--color-text-primary)",
                        fontSize: "14px",
                        fontFamily: "var(--font-mono)",
                        resize: "none",
                        outline: "none",
                        transition: "border-color 0.2s",
                    }}
                    onFocus={(e) => (e.target.style.borderColor = "var(--color-accent-blue)")}
                    onBlur={(e) => (e.target.style.borderColor = "var(--color-border)")}
                />
                {tickerCount < 2 && (
                    <p style={{ color: "var(--color-text-muted)", fontSize: "12px", marginTop: "8px" }}>
                        Select a preset or enter at least two tickers to run a scan.
                    </p>
                )}
            </div>

            {/* Period + Scan */}
            <div style={{ display: "flex", gap: "12px", alignItems: "flex-end" }}>
                <div style={{ flex: 1 }}>
                    <label style={{
                        display: "block",
                        fontSize: "12px",
                        fontWeight: 600,
                        color: "var(--color-text-secondary)",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        marginBottom: "8px",
                    }}>
                        Lookback Period
                    </label>
                    <div style={{ display: "flex", gap: "6px" }}>
                        {PERIODS.map((p) => (
                            <button
                                key={p.value}
                                onClick={() => handlePeriodChange(p.value)}
                                style={{
                                    flex: 1,
                                    padding: "10px 4px",
                                    borderRadius: "8px",
                                    fontSize: "13px",
                                    fontWeight: 500,
                                    cursor: "pointer",
                                    transition: "all 0.2s",
                                    border: period === p.value
                                        ? "1px solid var(--color-accent-blue)"
                                        : "1px solid var(--color-border)",
                                    background: period === p.value
                                        ? "rgba(99, 102, 241, 0.2)"
                                        : "transparent",
                                    color: period === p.value
                                        ? "var(--color-accent-cyan)"
                                        : "var(--color-text-muted)",
                                }}
                            >
                                {p.label}
                            </button>
                        ))}
                    </div>

                    {/* Custom Date Inputs */}
                    {period === "custom" && (
                        <div style={{ display: "flex", gap: "10px", marginTop: "12px", animation: "fadeIn 0.2s" }}>
                            <div style={{ flex: 1, position: "relative" }}>
                                <label style={{ display: "block", fontSize: "10px", color: "var(--color-text-muted)", marginBottom: "4px" }}>Start Date</label>
                                <ReactDatePicker
                                    selected={startDate}
                                    onChange={(date: Date | null) => handleCustomDateChange("start", date)}
                                    dateFormat="yyyy-MM-dd"
                                    placeholderText="YYYY-MM-DD"
                                    wrapperClassName="w-full"
                                    className="custom-date-picker"
                                    popperPlacement="bottom-start"
                                />
                            </div>
                            <div style={{ flex: 1, position: "relative" }}>
                                <label style={{ display: "block", fontSize: "10px", color: "var(--color-text-muted)", marginBottom: "4px" }}>End Date</label>
                                <ReactDatePicker
                                    selected={endDate}
                                    onChange={(date: Date | null) => handleCustomDateChange("end", date)}
                                    dateFormat="yyyy-MM-dd"
                                    placeholderText="YYYY-MM-DD"
                                    wrapperClassName="w-full"
                                    className="custom-date-picker"
                                    popperPlacement="bottom-end"
                                />
                            </div>
                        </div>
                    )}

                    {/* Interval Selection */}
                    {(period === "60d" || period === "6mo" || period === "1y" || period === "custom") && (
                        <div style={{ marginTop: "12px", animation: "fadeIn 0.2s" }}>
                            <label style={{
                                display: "block",
                                fontSize: "12px",
                                fontWeight: 600,
                                color: "var(--color-text-secondary)",
                                textTransform: "uppercase",
                                letterSpacing: "0.05em",
                                marginBottom: "8px",
                            }}>
                                Interval
                            </label>
                            <div style={{ display: "flex", gap: "6px" }}>
                                {getIntervalOptions(period, startDate, endDate).map((intVal) => (
                                    <button
                                        key={intVal}
                                        onClick={() => setIntervalVal(intVal)}
                                        style={{
                                            padding: "8px 16px",
                                            borderRadius: "8px",
                                            fontSize: "13px",
                                            fontWeight: 500,
                                            cursor: "pointer",
                                            transition: "all 0.2s",
                                            border: interval === intVal
                                                ? "1px solid var(--color-accent-blue)"
                                                : "1px solid var(--color-border)",
                                            background: interval === intVal
                                                ? "rgba(99, 102, 241, 0.2)"
                                                : "transparent",
                                            color: interval === intVal
                                                ? "var(--color-accent-cyan)"
                                                : "var(--color-text-muted)",
                                        }}
                                    >
                                        {intVal}
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                <div style={{ display: "flex", gap: "8px" }}>
                    <button
                        onClick={handleReset}
                        disabled={isScanning}
                        style={{
                            padding: "12px 24px",
                            borderRadius: "10px",
                            border: "1px solid var(--color-border)",
                            fontSize: "14px",
                            fontWeight: 600,
                            cursor: isScanning ? "not-allowed" : "pointer",
                            background: "transparent",
                            color: "var(--color-text-secondary)",
                            transition: "all 0.2s",
                        }}
                    >
                        Reset
                    </button>
                    <button
                        onClick={handleScan}
                        disabled={!canScan}
                        style={{
                            padding: "12px 32px",
                            borderRadius: "10px",
                            border: "none",
                            fontSize: "14px",
                            fontWeight: 600,
                            cursor: canScan ? "pointer" : "not-allowed",
                            background: isScanning
                                ? "rgba(99, 102, 241, 0.3)"
                                : "linear-gradient(135deg, #6366f1, #4f46e5)",
                            color: "#fff",
                            transition: "all 0.2s",
                            opacity: canScan ? 1 : 0.5,
                            display: "flex",
                            alignItems: "center",
                            gap: "8px",
                            whiteSpace: "nowrap",
                        }}
                    >
                        {isScanning && (
                            <span style={{
                                width: "16px",
                                height: "16px",
                                border: "2px solid rgba(255,255,255,0.3)",
                                borderTop: "2px solid #fff",
                                borderRadius: "50%",
                                display: "inline-block",
                            }} className="animate-spin" />
                        )}
                        {isScanning ? "Scanning..." : "Run Scan"}
                    </button>
                </div>
            </div>
        </div>
    );
}
