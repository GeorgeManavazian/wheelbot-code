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


def sr_variance_term(sr: float, skew: float, kurt_excess: float) -> float:
    """Mertens denominator: 1 - skew*SR + ((kurt_total - 1)/4)*SR^2.
    kurt_total - 1 == kurt_excess + 2, since pandas .kurt() returns excess.

    Not clamped. For any valid moment triple this is >= 0 -- substituting Pearson's
    inequality (kurt_excess >= skew^2 - 2) gives (1 - skew*SR/2)^2. It can only go
    negative when a bias-corrected SAMPLE kurtosis falls below that floor, which
    happens at n ~ 4-6. Callers must treat a non-positive value as degenerate and
    fail closed -- clamping it to 0 sends z to +inf and certifies noise as edge.
    """
    return 1 - skew * sr + ((kurt_excess + 2) / 4) * sr * sr


def moments_degenerate(sr: float, skew: float, kurt_excess: float) -> bool:
    return sr_variance_term(sr, skew, kurt_excess) <= 0.0


def zstat(sr: float, T: int, skew: float, kurt_excess: float, sr_benchmark: float = 0.0) -> float:
    """Bailey & Lopez de Prado z-statistic for an observed Sharpe (Ch.8, p.117).
    Degenerate moments return -inf, i.e. maximally insignificant."""
    term = sr_variance_term(sr, skew, kurt_excess)
    if term <= 0.0:
        return float("-inf")
    return float((sr - sr_benchmark) * sqrt(T - 1) / sqrt(term))


def single_trial_alpha(z: float) -> float:
    """Observed single-trial type-I error, alpha = Z[-z] (Ch.8, snippet 8.3)."""
    return float(norm.cdf(-z))


def _moments(returns: pd.Series, T: int | None):
    sr = returns.mean() / (returns.std(ddof=1) + 1e-12)
    T = T if (T is not None and T >= 4) else len(returns)
    return float(sr), T, float(returns.skew()), float(returns.kurt())


def observed_alpha(returns: pd.Series, T: int | None = None) -> float:
    """Single-trial alpha for a realized return series. Fails closed at 1.0."""
    returns = returns.dropna()
    if len(returns) < 4:
        return 1.0
    return single_trial_alpha(zstat(*_moments(returns, T)))


def deflated_sharpe(returns: pd.Series, K_effective: int, T: int | None = None) -> float:
    returns = returns.dropna()
    if len(returns) < 4:
        return 0.0
    sr, T, skew, kurt = _moments(returns, T)
    term = sr_variance_term(sr, skew, kurt)
    if term <= 0.0:
        return 0.0  # degenerate moments: fail closed, never certify
    e_max = expected_max_sr(K_effective, mean_sr=0.0, std_sr=1.0 / sqrt(T))
    z = (sr - e_max) * sqrt(T - 1) / sqrt(term)
    return float(norm.cdf(z))
