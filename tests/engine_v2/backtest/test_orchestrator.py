import numpy as np
import pandas as pd
import pytest
from src.engine_v2.backtest.orchestrator import run_backtest, BacktestConfig
from src.engine_v2.data.loader import load_bars

class ConstantLongStrat:
    display_name = "Const Long"
    mechanism = "test-only"
    parameter_grid = {}
    holding_period_cap = 1
    def __init__(self): pass
    def forecast(self, bars, asof):
        return pd.Series({"SPY": 10.0})

class BadPlugin:
    display_name = ""
    mechanism = ""
    parameter_grid = {}
    holding_period_cap = 1
    def forecast(self, bars, asof): return pd.Series(dtype=float)

def test_run_backtest_returns_verdict_dict():
    bars = load_bars(["SPY"], "2007-01-01", "2010-12-31")
    v = run_backtest(ConstantLongStrat, bars, config=BacktestConfig(n_folds=5, cpcv_k=2))
    assert set(["pass", "watch", "shelf", "dsr", "fwer", "k_effective",
                "regime_kill", "calmar_overall", "notes"]).issubset(v.keys())

def test_run_backtest_rejects_bad_plugin():
    bars = load_bars(["SPY"], "2007-01-01", "2010-12-31")
    with pytest.raises(Exception):  # PluginContractError
        run_backtest(BadPlugin, bars)

def test_run_backtest_rejects_out_of_cap_target_risk():
    bars = load_bars(["SPY"], "2007-01-01", "2010-12-31")
    with pytest.raises(Exception):  # AccountCapExceeded
        run_backtest(ConstantLongStrat, bars,
                     config=BacktestConfig(n_folds=5, cpcv_k=2, target_risk=0.35))
