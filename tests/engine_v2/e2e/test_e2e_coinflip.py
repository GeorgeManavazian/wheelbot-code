import numpy as np
import pandas as pd
from src.engine_v2.gate.verdict import compute_verdict

def test_coinflip_shelves():
    rng = np.random.default_rng(0)
    n = 400
    idx = pd.date_range("2010-01-01", periods=n, freq="B")
    trm = pd.DataFrame(rng.normal(0, 0.01, (n, 12)), index=idx,
                       columns=[f"t{i}" for i in range(12)])
    reg = pd.DataFrame({"regime_trend": ["bull"] * n,
                        "regime_vol": ["calm"] * n,
                        "regime_rate": ["falling"] * n}, index=idx)
    v = compute_verdict(trm, reg, calmar_overall=0.4)
    assert v["shelf"] and not v["pass"]
