"""Deflated Sharpe Ratio per Lopez de Prado Ch.8.
Uses measured skew + kurtosis (fat-tail deflation), Euler-Mascheroni gamma
for E[max SR_k]."""
from __future__ import annotations
import numpy as np
import pandas as pd
from math import sqrt
from scipy.stats import norm

EULER_MASCHERONI = 0.5772156649015329


def expected_max_sr(K: int, mean_sr: float = 0.0, std_sr: float = 1.0) -> float:
    if K <= 1:
        return mean_sr
    q1 = norm.ppf(1 - 1.0 / K)
    q2 = norm.ppf(1 - 1.0 / (K * np.e))
    emax = mean_sr + std_sr * ((1 - EULER_MASCHERONI) * q1 + EULER_MASCHERONI * q2)
    return float(emax)


def deflated_sharpe(returns: pd.Series, K_effective: int, T: int | None = None) -> float:
    returns = returns.dropna()
    if len(returns) < 2:
        return 0.0
    sr = returns.mean() / (returns.std(ddof=1) + 1e-12)
    T = T or len(returns)
    skew = float(returns.skew())
    kurt = float(returns.kurt())  # excess kurtosis
    e_max = expected_max_sr(K_effective, mean_sr=0.0, std_sr=1.0 / sqrt(T))
    num = (sr - e_max) * sqrt(T - 1)
    den = sqrt(1 - skew * sr + ((kurt) / 4) * sr * sr)
    z = num / (den + 1e-12)
    return float(norm.cdf(z))
