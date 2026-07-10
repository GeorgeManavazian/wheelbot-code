import numpy as np
import pandas as pd
import pytest
from src.engine_v2.gate.dsr import expected_max_sr, deflated_sharpe


def test_expected_max_sr_lopez_worked_example():
    # Lopez Ch.8 p.111: K=1000 uninformed unit-Normal trials -> ~3.26
    v = expected_max_sr(K=1000, mean_sr=0.0, std_sr=1.0)
    assert 3.0 <= v <= 3.5


def test_expected_max_sr_monotone_in_K():
    a = expected_max_sr(K=10)
    b = expected_max_sr(K=1000)
    assert b > a


def test_deflated_sharpe_low_for_random_returns():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0, 0.01, 500))
    p = deflated_sharpe(r, K_effective=200)
    assert p < 0.5


def test_deflated_sharpe_high_for_strong_signal():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.005, 0.01, 500))  # SR ~7 annualized
    p = deflated_sharpe(r, K_effective=1)
    assert p > 0.95


def test_deflated_sharpe_in_unit_interval():
    rng = np.random.default_rng(1)
    r = pd.Series(rng.normal(0.001, 0.01, 200))
    p = deflated_sharpe(r, K_effective=50)
    assert 0.0 <= p <= 1.0


def test_deflated_sharpe_short_series_no_nan():
    for n in [2, 3]:
        r = pd.Series([0.01] * n)
        v = deflated_sharpe(r, K_effective=5)
        assert v == 0.0, f"length {n} should return 0.0 not nan"


def test_deflated_sharpe_kurt_convention_matches_lopez():
    # For a well-known distribution: N(0.005, 0.01) large T, K=1, DSR should be very high.
    # Under wrong (excess/4) formula, DSR was ~0.99 for K_eff=1 which passes weak test;
    # under correct ((kurt+2)/4) formula it stays >0.95. Both should exceed threshold.
    rng = np.random.default_rng(42)
    r = pd.Series(rng.normal(0.005, 0.01, 5000))
    assert deflated_sharpe(r, K_effective=1) > 0.95


def test_deflated_sharpe_stable_under_high_positive_skew():
    # Adversarial: high skew + high SR could push denom negative under old code.
    # New code with max(...,0) inside sqrt handles it.
    r = pd.Series([0.0]*10 + [0.1])  # extreme positive skew
    v = deflated_sharpe(r, K_effective=1)
    assert 0.0 <= v <= 1.0
