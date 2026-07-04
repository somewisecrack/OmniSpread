from pydantic import BaseModel
from typing import Literal, Optional


class ScanRequest(BaseModel):
    tickers: list[str]
    period: str = "3y"
    interval: str = "1d"
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class BacktestRequest(BaseModel):
    x: str
    y: str
    qty: float
    direction: str
    interval: str = "1d"
    half_life: int
    end_date: str
    strategy: Literal["equity", "futures", "futures_options", "credit_spreads"] = "equity"


class CreditStructureRequest(BaseModel):
    x: str
    y: str
    qty: float
    direction: str


class PairResult(BaseModel):
    pair: str
    x: str = ""
    y: str = ""
    qty: float = 0.0
    direction: str = ""
    combo: str
    method: str
    price_corr: float
    z_score: float
    half_life: int
    move_to_mean: float
    exp_return: float
    unit_price: float
    hurst: float
    prob_profit: float
    prob_profit_low: float
    prob_profit_high: float
    same_sector: str
    extreme_z_in_hl: str
    extreme_z_detail: str
    profitable_since_extreme: str
    pnl_since_extreme: float
    historical_z_scores: list[dict] = []


class TaskResponse(BaseModel):
    task_id: str
    status: str
    results: list[PairResult] = []
