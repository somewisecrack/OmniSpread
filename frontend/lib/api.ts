const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface ScanRequest {
    tickers: string[];
    period: string;
    interval?: string;
    start_date?: string;
    end_date?: string;
}

export interface PairResult {
    pair: string;
    combo: string;
    method: string;
    price_corr: number;
    z_score: number;
    half_life: number;
    move_to_mean: number;
    exp_return: number;
    unit_price: number;
    hurst: number;
    prob_profit: number;
    prob_profit_low: number;
    prob_profit_high: number;
    same_sector: string;
    extreme_z_in_hl: string;
    extreme_z_detail: string;
    profitable_since_extreme: string;
    pnl_since_extreme: number;
    historical_z_scores: { time: number; value: number }[];
}

export interface TaskResult {
    task_id: string;
    status: "processing" | "completed" | "failed" | "not_found";
    results: PairResult[];
    error?: string;
}

export type Presets = Record<string, string[]>;

export async function startScan(request: ScanRequest): Promise<{ task_id: string }> {
    const res = await fetch(`${API_BASE}/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
    });
    if (!res.ok) throw new Error(`Scan request failed: ${res.statusText}`);
    return res.json();
}

export async function getResults(taskId: string): Promise<TaskResult> {
    const res = await fetch(`${API_BASE}/results/${taskId}`);
    if (!res.ok) throw new Error(`Results fetch failed: ${res.statusText}`);
    return res.json();
}

export async function getPresets(): Promise<Presets> {
    const res = await fetch(`${API_BASE}/presets`);
    if (!res.ok) throw new Error(`Presets fetch failed: ${res.statusText}`);
    return res.json();
}

export async function pollResults(
    taskId: string,
    onUpdate: (result: TaskResult) => void,
    intervalMs: number = 2000,
    maxAttempts: number = 300
): Promise<TaskResult> {
    let attempts = 0;
    while (attempts < maxAttempts) {
        const result = await getResults(taskId);
        onUpdate(result);
        if (result.status === "completed" || result.status === "failed") {
            return result;
        }
        await new Promise((resolve) => setTimeout(resolve, intervalMs));
        attempts++;
    }
    throw new Error("Polling timed out");
}
