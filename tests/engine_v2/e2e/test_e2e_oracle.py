import numpy as np
import pandas as pd
from src.engine_v2.gate.verdict import compute_verdict

def test_oracle_passes():
    n = 500
    idx = pd.date_range("2010-01-01", periods=n, freq="B")
    # strong constant edge, low noise: SR annualized ~= huge
    trm = pd.DataFrame(np.full((n, 8), 0.003), index=idx,
                       columns=[f"t{i}" for i in range(8)]) \
              + np.random.default_rng(0).normal(0, 0.001, (n, 8))
    reg = pd.DataFrame({"regime_trend": ["bull"] * n,
                        "regime_vol": ["calm"] * n,
                        "regime_rate": ["falling"] * n}, index=idx)
    v = compute_verdict(trm, reg, calmar_overall=1.8)
    assert v["pass"]
