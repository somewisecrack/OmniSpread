import uuid
import logging
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

from models import BacktestRequest, CreditStructureRequest, ScanRequest, TaskResponse
from engine import OmniSpreadEngine
from derivatives_backtest import build_credit_spread_structure, run_derivatives_backtest
from backtest_runner import run_equity_backtest
from presets import PRESETS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OmniSpreadAPI")

app = FastAPI(title="OmniSpread API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory task store
tasks: dict[str, dict] = {}


@app.get("/")
async def root():
    return {"app": "OmniSpread", "version": "1.0.0", "status": "running"}


@app.get("/presets")
async def get_presets():
    return PRESETS


@app.post("/scan")
async def start_scan(request: ScanRequest, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())
    tasks[task_id] = {"task_id": task_id, "status": "processing", "results": []}
    background_tasks.add_task(run_engine, task_id, request)
    logger.info(f"Scan started: {task_id} | tickers={request.tickers} period={request.period}")
    return {"task_id": task_id}


@app.get("/results/{task_id}")
async def get_results(task_id: str):
    task = tasks.get(task_id)
    if not task:
        return {"task_id": task_id, "status": "not_found", "results": []}
    return task


@app.post("/credit-spread-structure")
async def credit_spread_structure(request: CreditStructureRequest):
    try:
        from nselib import derivatives

        result = build_credit_spread_structure(
            x=request.x,
            y=request.y,
            qty=request.qty,
            direction=request.direction,
            fetch_future=derivatives.future_price_volume_data,
            fetch_option=derivatives.option_price_volume_data,
        )
        return {"status": "completed", **result}
    except Exception as exc:
        logger.exception("Credit spread structure failed")
        return {"status": "failed", "error": str(exc)}


@app.post("/backtest")
async def backtest_pair(request: BacktestRequest):
    if request.half_life < 1:
        return {"status": "failed", "error": "Half-life must be at least 1 bar"}

    if request.strategy != "equity":
        if request.interval != "1d":
            return {
                "status": "failed",
                "error": "Futures and options backtests are available only for daily scans.",
            }
        try:
            from nselib import derivatives

            result = run_derivatives_backtest(
                x=request.x,
                y=request.y,
                qty=request.qty,
                direction=request.direction,
                half_life=request.half_life,
                end_date=request.end_date,
                strategy=request.strategy,
                fetch_future=derivatives.future_price_volume_data,
                fetch_option=derivatives.option_price_volume_data,
            )
        except Exception as exc:
            logger.exception("Derivatives backtest failed")
            return {"status": "failed", "error": str(exc)}

        rows = result["points"]
        return {
            "status": "completed",
            "pair": f"{request.x.replace('.NS','')}/{request.y.replace('.NS','')}",
            "x": request.x,
            "y": request.y,
            "qty": request.qty,
            "direction": request.direction,
            "strategy": request.strategy,
            "interval": "1d",
            "half_life": request.half_life,
            "entry_time": rows[0]["time"],
            "exit_time": result["half_life_time"],
            "final_pnl": result["half_life_pnl"],
            "max_profit": result["half_life_max_profit"],
            "expiry_time": result["expiry_time"],
            "expiry_pnl": result["expiry_pnl"],
            "points": rows,
            "legs": result["legs"],
            "x_lots": result["x_lots"],
            "y_lots": result["y_lots"],
            "note": "Daily NSE closing prices; excludes brokerage, taxes, slippage, margin and financing costs.",
        }

    try:
        result = run_equity_backtest(
            x=request.x,
            y=request.y,
            qty=request.qty,
            direction=request.direction,
            interval=request.interval,
            half_life=request.half_life,
            end_date=request.end_date,
        )
    except ValueError as exc:
        return {"status": "failed", "error": str(exc)}

    return {
        "status": "completed",
        "pair": f"{request.x.replace('.NS','').replace('.BO','')}/{request.y.replace('.NS','').replace('.BO','')}",
        "x": request.x,
        "y": request.y,
        "qty": request.qty,
        "direction": request.direction,
        "interval": result["interval"],
        "half_life": request.half_life,
        "entry_time": result["entry_time"],
        "exit_time": result["exit_time"],
        "strategy": "equity",
        "final_pnl": result["final_pnl"],
        "max_profit": result["max_profit"],
        "points": result["points"],
        "note": "Forward data may contain fewer bars than half-life if Yahoo has not published enough bars yet.",
    }


def run_engine(task_id: str, request: ScanRequest):
    try:
        engine = OmniSpreadEngine(
            tickers=request.tickers,
            period=request.period,
            interval=request.interval,
            start_date=request.start_date,
            end_date=request.end_date,
        )
        results = engine.run_scan()
        tasks[task_id]["results"] = results
        tasks[task_id]["status"] = "completed"
        logger.info(f"Scan completed: {task_id} | {len(results)} pairs found")
    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)
        logger.error(f"Scan failed: {task_id} | {e}")
