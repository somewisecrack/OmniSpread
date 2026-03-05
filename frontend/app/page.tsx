"use client";

import { useState } from "react";
import ScanForm from "@/components/scan-form";
import ResultsTable from "@/components/results-table";
import PairDetailModal from "@/components/pair-detail-modal";
import { startScan, pollResults, type PairResult } from "@/lib/api";

export default function Home() {
  const [results, setResults] = useState<PairResult[]>([]);
  const [isScanning, setIsScanning] = useState(false);
  const [selectedPair, setSelectedPair] = useState<PairResult | null>(null);
  const [scanStatus, setScanStatus] = useState<string>("");
  const [error, setError] = useState<string>("");

  const handleScan = async (tickers: string[], period: string, interval?: string, startDate?: string, endDate?: string) => {
    setIsScanning(true);
    setResults([]);
    setError("");
    setScanStatus("Starting scan...");

    try {
      const { task_id } = await startScan({ tickers, period, interval, start_date: startDate, end_date: endDate });
      setScanStatus("Scanning pairs — this may take a moment...");

      const result = await pollResults(task_id, (update) => {
        if (update.status === "processing") {
          setScanStatus("Analyzing cointegration & running Monte Carlo...");
        }
      });

      if (result.status === "completed") {
        setResults(result.results);
        setScanStatus(
          result.results.length > 0
            ? `Found ${result.results.length} actionable pair${result.results.length > 1 ? "s" : ""}`
            : "No pairs met the Z > 2.0 threshold"
        );
      } else if (result.status === "failed") {
        setError(result.error || "Scan failed unexpectedly");
        setScanStatus("");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
      setScanStatus("");
    } finally {
      setIsScanning(false);
    }
  };

  return (
    <main style={{ maxWidth: "1200px", margin: "0 auto", padding: "40px 24px 80px" }}>
      {/* Header */}
      <header style={{ textAlign: "center", marginBottom: "48px" }}>
        <div style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "12px",
          marginBottom: "16px",
        }}>
          <div style={{
            width: "48px",
            height: "48px",
            borderRadius: "14px",
            background: "linear-gradient(135deg, #6366f1, #4f46e5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: "24px",
            fontWeight: 800,
          }} className="animate-pulse-glow">
            ⇌
          </div>
          <h1 style={{
            fontSize: "36px",
            fontWeight: 800,
            letterSpacing: "-0.03em",
            lineHeight: 1,
          }}>
            <span className="gradient-text">OMNISPREAD</span>
          </h1>
        </div>
        <p style={{
          color: "var(--color-text-secondary)",
          fontSize: "16px",
          maxWidth: "500px",
          margin: "0 auto",
        }}>
          Statistical Pairs Trading Scanner
        </p>
        <p style={{
          color: "var(--color-text-muted)",
          fontSize: "13px",
          marginTop: "8px",
        }}>
          Kalman-filtered cointegration • Monte Carlo P(profit) • Hurst exponent
        </p>
      </header>

      {/* Scan Form */}
      <section style={{ marginBottom: "32px" }}>
        <ScanForm onScan={handleScan} isScanning={isScanning} />
      </section>

      {/* Status */}
      {scanStatus && (
        <div style={{
          textAlign: "center",
          padding: "12px",
          marginBottom: "24px",
          fontSize: "14px",
          color: "var(--color-text-secondary)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: "8px",
        }}>
          {isScanning && (
            <span style={{
              width: "14px",
              height: "14px",
              border: "2px solid rgba(99,102,241,0.3)",
              borderTop: "2px solid var(--color-accent-blue)",
              borderRadius: "50%",
              display: "inline-block",
            }} className="animate-spin" />
          )}
          {!isScanning && results.length > 0 && (
            <span style={{ color: "var(--color-accent-green)" }}>✓</span>
          )}
          {scanStatus}
        </div>
      )}

      {/* Error */}
      {error && (
        <div style={{
          padding: "14px 20px",
          borderRadius: "12px",
          background: "rgba(248, 113, 113, 0.08)",
          border: "1px solid rgba(248, 113, 113, 0.2)",
          color: "var(--color-accent-red)",
          fontSize: "14px",
          marginBottom: "24px",
          textAlign: "center",
        }}>
          {error}
        </div>
      )}

      {/* Results */}
      <ResultsTable
        results={results}
        isLoading={isScanning}
        onRowClick={setSelectedPair}
      />

      {/* Modal */}
      <PairDetailModal pair={selectedPair} onClose={() => setSelectedPair(null)} />

      {/* Footer */}
      <footer style={{
        textAlign: "center",
        marginTop: "48px",
        fontSize: "12px",
        color: "var(--color-text-muted)",
      }}>
        <p>OMNISPREAD — For educational and research purposes only. Not financial advice.</p>
      </footer>
    </main>
  );
}
