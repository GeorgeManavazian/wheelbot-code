import numpy as np
import pandas as pd
import pytest
from src.engine_v2.gate.regime_eval import per_regime_sharpe, regime_kill

def _mk_regime(n):
    idx = pd.date_range("2010-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "regime_trend": (["bull"] * (n // 2)) + (["bear"] * (n - n // 2)),
        "regime_vol":   (["calm"] * n),
        "regime_rate":  (["falling"] * n),
    }, index=idx)

def test_per_regime_sharpe_shape():
    n = 500
    r = pd.Series(np.random.default_rng(0).normal(0, 0.01, n),
                  index=pd.date_range("2010-01-01", periods=n, freq="B"))
    reg = _mk_regime(n)
    out = per_regime_sharpe(r, reg)
    assert "sharpe" in out.columns
    assert "sample_pct" in out.columns

def test_regime_kill_fires_on_negative_big_cell():
    idx = pd.date_range("2010-01-01", periods=500, freq="B")
    r = pd.Series(np.concatenate([np.full(250, 0.001), np.full(250, -0.001)]), index=idx)
    reg = _mk_regime(500)
    out = per_regime_sharpe(r, reg)
    assert regime_kill(out) is True

def test_regime_kill_passes_when_all_positive():
    idx = pd.date_range("2010-01-01", periods=500, freq="B")
    r = pd.Series(np.full(500, 0.001), index=idx)
    reg = _mk_regime(500)
    out = per_regime_sharpe(r, reg)
    assert regime_kill(out) is False

def test_small_cell_ignored():
    # 1% sample cell can be negative w/o triggering kill
    idx = pd.date_range("2010-01-01", periods=500, freq="B")
    r = pd.Series(np.full(500, 0.001), index=idx)
    r.iloc[:5] = -0.05  # 1% bad cell
    reg = _mk_regime(500)
    reg.iloc[:5, 0] = "bear"
    out = per_regime_sharpe(r, reg)
    # bull sample dominates + positive; the 5 bear pts are <5% -> ignored by kill
    assert regime_kill(out, min_sample_pct=0.20) is False
