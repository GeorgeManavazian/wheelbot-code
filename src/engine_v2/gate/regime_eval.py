"""Per-regime Sharpe + kill rule. Cells collapsed to (regime_trend, regime_vol);
`regime_rate` is a fixture placeholder until real yield ingest lands."""
from __future__ import annotations
import numpy as np
import pandas as pd


def per_regime_sharpe(returns: pd.Series, regime_labels: pd.DataFrame) -> pd.DataFrame:
    df = pd.concat([returns.rename("ret"), regime_labels], axis=1).dropna()
    total = len(df)
    if total == 0:
        return pd.DataFrame(columns=["sharpe", "sample_pct"])
    grouped = df.groupby(["regime_trend", "regime_vol"])
    rows = []
    for key, g in grouped:
        sr = g["ret"].mean() / (g["ret"].std(ddof=1) + 1e-12) * np.sqrt(252)
        rows.append({"cell": "|".join(key), "sharpe": float(sr), "sample_pct": len(g) / total})
    return pd.DataFrame(rows).set_index("cell")


def regime_kill(per_regime: pd.DataFrame, min_sample_pct: float = 0.20) -> bool:
    for _, row in per_regime.iterrows():
        if row["sample_pct"] >= min_sample_pct and row["sharpe"] < 0:
            return True
    return False
