from pydantic import BaseModel
from typing import Literal, Optional


class ScanRequest(BaseModel):
    tickers: list[str]
    period: str = "3y"
    interval: str = "1d"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    price_basis: Literal["raw", "log"] = "raw"
    engine_version: Literal["v1", "v2"] = "v2"


class BacktestRequest(BaseModel):
    x: str
    y: str
    qty: float
    direction: str
    interval: str = "1d"
    half_life: int
    end_date: str
    strategy: Literal["equity", "futures", "futures_options", "credit_spreads"] = "equity"
    strike_rule: Literal["legacy", "vol"] = "vol"
    sold_sd: float = 1.0
    hedge_sd: float = 2.5


class CreditStructureRequest(BaseModel):
    x: str
    y: str
    qty: float
    direction: str
    strike_rule: Literal["legacy", "vol"] = "vol"
    sold_sd: float = 1.0
    hedge_sd: float = 2.5


class PairResult(BaseModel):
    pair: str
    x: str = ""
    y: str = ""
    qty: float = 0.0
    direction: str = ""
    combo: str
    method: str
    engine_version: str = "v1"
    price_basis: Literal["raw", "log"] = "raw"
    cadf_pvalue: Optional[float] = None
    johansen_rank: Optional[int] = None
    beta: Optional[float] = None
    price_corr: float
    z_score: float
    half_life: int
    move_to_mean: float
    exp_return: float
    unit_price: float
    # v1 only; absent on v2 rows.
    hurst: Optional[float] = None
    prob_profit: Optional[float] = None
    prob_profit_low: Optional[float] = None
    prob_profit_high: Optional[float] = None
    same_sector: str
    extreme_z_in_hl: str
    extreme_z_detail: str
    profitable_since_extreme: str
    pnl_since_extreme: float
    historical_z_scores: list[dict] = []
    # NOTE: the display-only volatility fields (x_atm_ivp_250d / y_atm_ivp_250d and
    # x_atm_vrp_21d / y_atm_vrp_21d) are intentionally NOT declared here. They are
    # present only on the standard 1y/1d scan and must be ABSENT for every other
    # scan. Declaring them with a default would make a pydantic response_model emit
    # them as null on ineligible scans, violating that contract. The /results route
    # returns the raw engine dict, so the keys are genuinely present-or-absent.


class TaskResponse(BaseModel):
    task_id: str
    status: str
    results: list[PairResult] = []
