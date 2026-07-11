"""Oracle e2e: a single genuine-edge trial passes the gate.
Uses ONE trial column (K_eff=1) so FWER = 0.05 exactly and the strategy
can PASS if DSR + Calmar + no-regime-kill all hold. This is the theoretical
best case: a real edge with no multiple testing.
"""
import numpy as np
import pandas as pd
from src.engine_v2.gate.verdict import compute_verdict


def test_single_trial_hits_kmin_guard():
    """K_MIN=5 means single-trial cases always return insufficient_trials
    and shelf=True. Verifies the short-circuit path.
    """
    n = 800
    idx = pd.date_range("2010-01-01", periods=n, freq="B")
    rng = np.random.default_rng(42)
    ret = np.full(n, 0.003) + rng.normal(0, 0.001, n)
    trm = pd.DataFrame({"t0": ret}, index=idx)
    reg = pd.DataFrame(
        {"regime_trend": ["bull"] * n,
         "regime_vol": ["calm"] * n,
         "regime_rate": ["falling"] * n},
        index=idx,
    )
    v = compute_verdict(trm, reg, calmar_overall=2.5)
    assert v["notes"].get("insufficient_trials") is True
    assert v["shelf"] is True
    assert v["pass"] is False


def test_five_oracles_produce_strong_dsr():
    """5 genuine-oracle trials with real edge (mean=0.003, sd=0.001, T=800).
    Under FWER correction with K_eff>=1, DSR should still be >0.90 given the
    signal strength. Verdict state machine exclusivity is checked too.
    """
    n = 800
    idx = pd.date_range("2010-01-01", periods=n, freq="B")
    rng = np.random.default_rng(0)
    ret_matrix = np.full((n, 5), 0.003) + rng.normal(0, 0.001, (n, 5))
    trm = pd.DataFrame(ret_matrix, index=idx, columns=[f"t{i}" for i in range(5)])
    reg = pd.DataFrame(
        {"regime_trend": ["bull"] * n,
         "regime_vol": ["calm"] * n,
         "regime_rate": ["falling"] * n},
        index=idx,
    )
    v = compute_verdict(trm, reg, calmar_overall=2.5)
    assert v["dsr"] > 0.90
    # verdict states are mutually exclusive
    states = [v["pass"], v["watch"], v["shelf"]]
    assert sum(states) == 1
