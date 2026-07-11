import numpy as np
import pandas as pd
from src.engine_v2.gate.verdict import compute_verdict

def test_bull_only_oracle_shelved_by_regime():
    n = 400
    idx = pd.date_range("2010-01-01", periods=n, freq="B")
    trend = ["bull"] * (n // 2) + ["bear"] * (n - n // 2)
    ret = np.where(np.array(trend) == "bull", 0.002, -0.002)
    trm = pd.DataFrame({f"t{i}": ret + np.random.default_rng(i).normal(0, 0.0005, n)
                        for i in range(8)}, index=idx)
    reg = pd.DataFrame({"regime_trend": trend,
                        "regime_vol": ["calm"] * n,
                        "regime_rate": ["falling"] * n}, index=idx)
    v = compute_verdict(trm, reg, calmar_overall=1.2)
    assert v["regime_kill"] and not v["pass"]
