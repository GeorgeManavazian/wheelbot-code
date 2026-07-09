import numpy as np
import pandas as pd
import pytest
from src.engine_v2.gate.fwer import fwer, k_effective, onc_cluster

def test_fwer_formula_k1():
    assert fwer(1, 0.05) == pytest.approx(0.05)

def test_fwer_grows_with_k():
    assert fwer(20, 0.05) > fwer(5, 0.05)

def test_fwer_at_k100_alpha05():
    assert fwer(100, 0.05) == pytest.approx(1 - 0.95 ** 100)

def test_onc_recovers_block_structure():
    rng = np.random.default_rng(0)
    T = 500
    # 3 blocks of 5 correlated series
    block1 = rng.normal(0, 1, T)
    block2 = rng.normal(0, 1, T)
    block3 = rng.normal(0, 1, T)
    cols = {}
    for i in range(5): cols[f"a{i}"] = block1 + rng.normal(0, 0.05, T)
    for i in range(5): cols[f"b{i}"] = block2 + rng.normal(0, 0.05, T)
    for i in range(5): cols[f"c{i}"] = block3 + rng.normal(0, 0.05, T)
    df = pd.DataFrame(cols)
    k = k_effective(df)
    assert 2 <= k <= 4

def test_k_effective_of_iid_is_high():
    rng = np.random.default_rng(1)
    df = pd.DataFrame(rng.normal(0, 1, (500, 20)))
    df.columns = [f"t{i}" for i in range(20)]
    k = k_effective(df)
    assert k >= 5
