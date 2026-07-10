import numpy as np
import pandas as pd
import pytest
from src.engine_v2.sizing.nco import mp_denoise, nco_weights, NCO_MIN_INSTRUMENTS

def _synthetic_cov(n, T, rng):
    X = rng.normal(0, 1, (T, n))
    # inject one strong common factor to give MP something to compress
    factor = rng.normal(0, 1, T).reshape(-1, 1)
    X += factor * rng.normal(0, 0.5, n)
    return pd.DataFrame(X, columns=[f"a{i}" for i in range(n)]).cov()

def test_mp_denoise_preserves_shape_and_symmetry():
    rng = np.random.default_rng(0)
    cov = _synthetic_cov(8, 500, rng)
    d = mp_denoise(cov, T=500)
    assert d.shape == cov.shape
    assert np.allclose(d.values, d.values.T, atol=1e-8)

@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
def test_mp_denoise_reduces_condition_number(seed):
    rng = np.random.default_rng(seed)
    cov = _synthetic_cov(10, 400, rng)
    k_raw = np.linalg.cond(cov.values)
    k_den = np.linalg.cond(mp_denoise(cov, T=400).values)
    assert k_den <= k_raw

def test_nco_weights_sum_to_one():
    rng = np.random.default_rng(0)
    cov = _synthetic_cov(8, 500, rng)
    w = nco_weights(cov)
    assert w.sum() == pytest.approx(1.0, abs=1e-6)
    assert (w >= 0).all()

def test_nco_min_instruments_constant():
    assert NCO_MIN_INSTRUMENTS == 6
