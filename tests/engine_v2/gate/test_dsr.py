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
