"""Marcenko-Pastur denoise + Nested Clustered Optimization (Lopez Ch.2, Ch.7).
Kicks in for portfolios with >= 6 instruments. Below that, simpler inverse-vol
suffices."""
from __future__ import annotations
import numpy as np
import pandas as pd
from ..gate.fwer import onc_cluster

NCO_MIN_INSTRUMENTS = 6

def _mp_bounds(N: int, T: int, sigma2: float = 1.0) -> tuple[float, float]:
    q = N / T
    lam_minus = sigma2 * (1 - np.sqrt(q)) ** 2
    lam_plus  = sigma2 * (1 + np.sqrt(q)) ** 2
    return lam_minus, lam_plus

def mp_denoise(cov: pd.DataFrame, T: int) -> pd.DataFrame:
    N = cov.shape[0]
    stds = np.sqrt(np.diag(cov.values))
    corr = cov.values / np.outer(stds, stds)
    eigvals, eigvecs = np.linalg.eigh(corr)
    order = np.argsort(eigvals)[::-1]
    eigvals, eigvecs = eigvals[order], eigvecs[:, order]
    _, lam_plus = _mp_bounds(N, T)
    noise_mask = eigvals < lam_plus
    if noise_mask.any():
        avg = eigvals[noise_mask].mean()
        eigvals = np.where(noise_mask, avg, eigvals)
    corr_denoised = np.einsum('ij,j,kj->ik', eigvecs, eigvals, eigvecs)
    corr_denoised = (corr_denoised + corr_denoised.T) / 2
    d = np.sqrt(np.diag(corr_denoised))
    corr_denoised = corr_denoised / np.outer(d, d)  # restore unit diagonal
    cov_denoised = corr_denoised * np.outer(stds, stds)
    return pd.DataFrame(cov_denoised, index=cov.index, columns=cov.columns)

def _inverse_variance(cov: pd.DataFrame) -> pd.Series:
    iv = 1 / np.diag(cov.values)
    w = iv / iv.sum()
    return pd.Series(w, index=cov.index)

def nco_weights(cov: pd.DataFrame, mu: pd.Series | None = None) -> pd.Series:
    T = max(cov.shape[0] * 5, 100)  # conservative estimate when caller does not pass T
    cov_d = mp_denoise(cov, T=T)
    stds = np.sqrt(np.diag(cov_d.values))
    corr_d = cov_d.values / np.outer(stds, stds)
    corr_df = pd.DataFrame(corr_d, index=cov.index, columns=cov.columns)
    _, labels = onc_cluster(corr_df)
    clusters: dict[int, list[str]] = {}
    for name, lbl in zip(cov.index, labels):
        clusters.setdefault(int(lbl), []).append(name)
    intra_w = pd.Series(0.0, index=cov.index)
    cluster_cov = pd.DataFrame(0.0, index=list(clusters), columns=list(clusters))
    for cid, members in clusters.items():
        sub = cov_d.loc[members, members]
        w = _inverse_variance(sub)
        intra_w.loc[members] = w
        cluster_var = float(w @ sub.values @ w.values)
        cluster_cov.loc[cid, cid] = cluster_var
    inter_w = _inverse_variance(cluster_cov)
    final = pd.Series(0.0, index=cov.index)
    for cid, members in clusters.items():
        final.loc[members] = intra_w.loc[members] * inter_w.loc[cid]
    final = final / final.sum()
    return final
