"""FWER (Sidak) + effective K via Optimal-Number-of-Clusters (Lopez Ch.4/Ch.8).
K_effective = # clusters of trial returns; correlated trials collapse into one."""
from __future__ import annotations
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_samples

def fwer(K_effective: int, alpha: float = 0.05) -> float:
    if K_effective <= 0:
        return 0.0
    return float(1 - (1 - alpha) ** K_effective)

def _dist_from_corr(corr: pd.DataFrame) -> np.ndarray:
    return np.sqrt(np.clip((1 - corr.values) / 2, 0, 1))

def onc_cluster(corr: pd.DataFrame, max_k: int | None = None) -> tuple[int, np.ndarray]:
    n = corr.shape[0]
    max_k = max_k or max(2, n // 2)
    D = _dist_from_corr(corr)
    best_q = -np.inf
    best_k = 1
    best_labels = np.zeros(n, dtype=int)
    for k in range(2, max_k + 1):
        if k >= n:
            break
        km = KMeans(n_clusters=k, n_init=5, random_state=0).fit(D)
        try:
            s = silhouette_samples(D, km.labels_, metric="precomputed")
        except ValueError:
            continue
        q = s.mean() / (s.std() + 1e-12)
        if q > best_q:
            best_q, best_k, best_labels = q, k, km.labels_
    return best_k, best_labels

def k_effective(trial_return_matrix: pd.DataFrame) -> int:
    if trial_return_matrix.shape[1] < 2:
        return 1
    corr = trial_return_matrix.corr().fillna(0)
    k, _ = onc_cluster(corr)
    return int(k)
