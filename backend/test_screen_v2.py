"""Tests for the v2 scan: CADF + Johansen rank 1, static hedge, all pairs, no Monte Carlo.

All data is synthetic with fixed seeds; nothing touches the network.
"""
import importlib.util
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from engine import OmniSpreadEngine

N = 750
IDX = pd.date_range("2020-01-01", periods=N, freq="B")


def _ar1(rng, n, phi, sd):
    e = np.zeros(n)
    for t in range(1, n):
        e[t] = phi * e[t - 1] + rng.normal(0, sd)
    return e


def raw_coint(seed, beta=0.8, phi=0.95, sd=0.5, shock=0.0):
    rng = np.random.default_rng(seed)
    x = 100 + np.cumsum(rng.normal(0, 1, N))
    gap = _ar1(rng, N, phi, sd)
    gap[-1] += shock
    return x, 5 + beta * x + gap


def log_coint(seed, beta=0.8, phi=0.95, sd=0.01, shock=0.0, x0=100.0, y0=50.0):
    rng = np.random.default_rng(seed)
    lx = np.log(x0) + np.cumsum(rng.normal(0, 0.02, N))
    gap = _ar1(rng, N, phi, sd)
    gap[-1] += shock
    ly = np.log(y0) + beta * (lx - np.log(x0)) + gap
    return np.exp(lx), np.exp(ly)


def raw_null(seed):
    rng = np.random.default_rng(seed)
    return 100 + np.cumsum(rng.normal(0, 1, N)), 100 + np.cumsum(rng.normal(0, 1, N))


def log_null(seed):
    rng = np.random.default_rng(seed)
    return (100 * np.exp(np.cumsum(rng.normal(0, 0.02, N))),
            50 * np.exp(np.cumsum(rng.normal(0, 0.02, N))))


def make_engine(x, y, basis="raw", z_filter=True):
    eng = OmniSpreadEngine(tickers=["X", "Y"], price_basis=basis, engine_version="v2")
    eng.data = pd.DataFrame({"X": x, "Y": y}, IDX)
    if not z_filter:
        eng.Z_SCORE_LIMIT = -1.0  # isolate the cointegration stage
    return eng


# --- Johansen rank rule ---

def _crit(c0, c1):
    return np.array([[0, c0, 0], [0, c1, 0]], dtype=float)


def test_johansen_rank_logic():
    cv = _crit(15.0, 4.0)
    rank = OmniSpreadEngine.johansen_rank
    assert rank(np.array([10.0, 1.0]), np.array([9.0, 1.0]), cv, cv) == 0
    assert rank(np.array([20.0, 1.0]), np.array([19.0, 1.0]), cv, cv) == 1
    assert rank(np.array([30.0, 8.0]), np.array([22.0, 8.0]), cv, cv) == 2
    # trace rejects r=0 but max-eigenvalue does not -> rank 0
    assert rank(np.array([20.0, 1.0]), np.array([10.0, 1.0]), cv, cv) == 0


# --- Cointegration stage ---

@pytest.mark.parametrize("basis,gen", [("raw", raw_coint), ("log", log_coint)])
def test_cointegrated_pair_passes_with_correct_hedge(basis, gen):
    x, y = gen(seed=1)
    item = make_engine(x, y, basis, z_filter=False).screen_pair("X", "Y")
    assert item is not None
    assert item["method"] == "CADF+Johansen"
    assert item["johansen_rank"] >= 1
    assert item["cadf_pvalue"] < 0.05
    assert item["beta"] == pytest.approx(0.8, rel=0.10)


@pytest.mark.parametrize("basis,gen", [("raw", raw_null), ("log", log_null)])
def test_unrelated_random_walks_rarely_pass(basis, gen):
    seeds = range(200)
    passed = sum(make_engine(*gen(s), basis, z_filter=False).screen_pair("X", "Y") is not None
                 for s in seeds)
    # v1's Kalman CADF passed 92-100% of these; a correct 5% test passes ~5% or fewer.
    assert passed / len(seeds) <= 0.08


def test_johansen_rank_2_is_accepted():
    # A genuine cointegrated pair that Johansen (det_order=0) labels rank 2:
    # it must still pass, since only the spread needs to be stationary.
    rng = np.random.default_rng(5)
    x = 10000 + np.cumsum(rng.normal(0, 50, N))
    y = 30 + 0.003 * x + _ar1(rng, N, 0.95, 0.02)
    item = make_engine(x, y, "raw", z_filter=False).screen_pair("X", "Y")
    assert item is not None
    assert item["johansen_rank"] == 2


def test_negative_beta_rejected():
    rng = np.random.default_rng(4)
    x = 100 + np.cumsum(rng.normal(0, 1, N))
    y = 200 - 0.8 * x + _ar1(rng, N, 0.95, 0.5)
    assert make_engine(x, y, "raw", z_filter=False).screen_pair("X", "Y") is None


# --- Share ratio / precision ---

def test_raw_qty_equals_beta():
    x, y = raw_coint(seed=1)
    item = make_engine(x, y, "raw", z_filter=False).screen_pair("X", "Y")
    assert item["qty"] == item["beta"]


def test_log_qty_is_share_ratio():
    x, y = log_coint(seed=1)
    item = make_engine(x, y, "log", z_filter=False).screen_pair("X", "Y")
    assert item["qty"] == pytest.approx(item["beta"] * item["py"] / item["px"])
    assert item["px"] == x[-1] and item["py"] == y[-1]  # not rounded


def test_small_hedge_not_rounded_to_zero():
    rng = np.random.default_rng(6)
    x = 10000 + np.cumsum(rng.normal(0, 50, N))
    y = 30 + 0.003 * x + _ar1(rng, N, 0.95, 0.02)
    item = make_engine(x, y, "raw", z_filter=False).screen_pair("X", "Y")
    assert item is not None
    assert item["qty"] == pytest.approx(0.003, rel=0.15)
    assert "Buy 0.00 " not in item["combo_str"] and "Sell 0.00 " not in item["combo_str"]


def test_sub_cent_prices_kept():
    x, y = log_coint(seed=1, x0=0.004, y0=2.0)
    item = make_engine(x, y, "log", z_filter=False).screen_pair("X", "Y")
    assert item is not None
    assert item["px"] == x[-1] and item["px"] < 0.01
    assert "(0.00," not in item["combo_str"]


# --- Signal, full scan, output ---

def test_shocked_pair_accepted_by_full_screen():
    x, y = raw_coint(seed=1, shock=6.0)
    item = make_engine(x, y, "raw").screen_pair("X", "Y")
    assert item is not None
    assert abs(item["half_life"]) >= 6
    assert item["direction"] == "SHORT_SPREAD"


def test_scan_returns_all_pairs_ranked_and_no_monte_carlo(monkeypatch):
    rng = np.random.default_rng(7)
    cols = {f"R{i}": 100 + np.cumsum(rng.normal(0, 1, N)) for i in range(6)}
    x, y = raw_coint(seed=1, shock=6.0)
    x2, y2 = raw_coint(seed=2, shock=-6.0)
    cols.update({"A": x2, "B": y2, "X": x, "Y": y})  # real pairs placed last
    eng = OmniSpreadEngine(tickers=list(cols), price_basis="raw", engine_version="v2", top_n=1)
    eng.data = pd.DataFrame(cols, IDX)

    def boom(*a, **k):
        raise AssertionError("v2 must not run the Monte Carlo")
    monkeypatch.setattr(eng, "run_ensemble_mc", boom)

    results = eng._run_scan_v2(list(cols))
    pairs = {(r["x"], r["y"]) for r in results}
    assert {("A", "B"), ("X", "Y")} <= pairs          # found despite top_n=1 and list order
    pvals = [r["cadf_pvalue"] for r in results]
    assert pvals == sorted(pvals)
    for r in results:
        assert r["engine_version"] == "v2"
        for gone in ("prob_profit", "prob_profit_low", "prob_profit_high", "hurst"):
            assert gone not in r


def test_invalid_engine_version():
    with pytest.raises(ValueError):
        OmniSpreadEngine(tickers=[], engine_version="v3")


# --- v1 unchanged ---

def _load_head_engine():
    repo = Path(__file__).resolve().parent.parent
    try:
        # cc5e5b5 = last commit before the v2 scan was added.
        src = subprocess.run(["git", "show", "cc5e5b5:backend/engine.py"], cwd=repo,
                             capture_output=True, text=True, check=True).stdout
    except Exception:
        pytest.skip("pre-v2 engine not available from git")
    path = Path(__file__).resolve().parent / ".head_engine_tmp.py"
    path.write_text(src)
    try:
        spec = importlib.util.spec_from_file_location("head_engine", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        path.unlink(missing_ok=True)
    return mod.OmniSpreadEngine


@pytest.mark.parametrize("basis", ["raw", "log"])
def test_v1_default_matches_previous_engine(basis):
    Old = _load_head_engine()
    for seed in range(15):
        x, y = (raw_coint if basis == "raw" else log_coint)(seed, shock=0.0)
        rng = np.random.default_rng(100 + seed)
        y = y * np.exp(rng.normal(0, 0.002, N))  # vary the data a little
        df = pd.DataFrame({"X": x, "Y": y}, IDX)
        new = OmniSpreadEngine(tickers=["X", "Y"], price_basis=basis)  # default = v1
        old = Old(tickers=["X", "Y"], price_basis=basis)
        new.data, old.data = df, df
        a, b = new.screen_pair("X", "Y"), old.screen_pair("X", "Y")
        assert (a is None) == (b is None)
        if a is not None:
            for k in ("qty", "direction", "method", "combo_str", "px", "py"):
                assert a[k] == b[k]
            pd.testing.assert_series_equal(a["spread"], b["spread"])
