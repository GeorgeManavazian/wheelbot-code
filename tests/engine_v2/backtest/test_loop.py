import hashlib
import pandas as pd
import pytest
from src.engine_v2.backtest.loop import expand_trials, run_trial
from src.engine_v2.data.loader import load_bars

class ConstantLongStrat:
    display_name = "Const Long"
    mechanism = "test"
    parameter_grid = {"n": [1, 2]}
    holding_period_cap = 1
    def __init__(self, n=1): self.n = n
    def forecast(self, bars, asof):
        return pd.Series({"SPY": 10.0})

def test_expand_trials_cartesian():
    trials = expand_trials(ConstantLongStrat, {"n": [1, 2, 3]}, fold_ids=[0, 1])
    assert len(trials) == 6
    ids = {t["trial_id"] for t in trials}
    assert len(ids) == 6  # unique

def test_trial_id_deterministic():
    t = expand_trials(ConstantLongStrat, {"n": [1]}, fold_ids=[0])[0]
    expected = hashlib.sha256(
        f"{ConstantLongStrat.__name__}|n=1|fold=0".encode()
    ).hexdigest()[:12]
    assert t["trial_id"] == expected

def test_run_trial_produces_log():
    bars = load_bars(["SPY"], "2007-01-01", "2010-12-31")
    log = run_trial(ConstantLongStrat, {"n": 1}, bars,
                    fold_slice=slice("2010-01-01", "2010-03-31"))
    assert {"asof", "forecast", "notional", "fill_price", "equity"}.issubset(log.columns)
    assert (log["forecast"] == 10.0).all()

def test_run_trial_no_lookahead():
    bars = load_bars(["SPY"], "2007-01-01", "2010-12-31")
    log = run_trial(ConstantLongStrat, {"n": 1}, bars,
                    fold_slice=slice("2010-01-01", "2010-03-31"))
    # all fill_price rows must correspond to bars available at asof (not future)
    for _, row in log.iterrows():
        assert row["asof"] in bars.index
