"""Aggregate DSR + FWER + per-regime into pass/watch/shelf verdict.
Persist sealed to results/<run_id>/verdict.json (chmod 444, no overwrite)."""
from __future__ import annotations
import json
import pathlib
import stat
import pandas as pd
from .dsr import deflated_sharpe, moments_degenerate, observed_alpha
from .fwer import fwer, k_effective
from .regime_eval import per_regime_sharpe, regime_kill

DSR_PASS = 0.95
DSR_WATCH_LO = 0.80
FWER_PASS = 0.05
K_MIN = 5

def _best_trial_returns(trial_return_matrix: pd.DataFrame) -> pd.Series:
    sharpes = trial_return_matrix.apply(lambda r: r.mean() / (r.std(ddof=1) + 1e-12))
    return trial_return_matrix[sharpes.idxmax()]


def _is_degenerate(returns: pd.Series) -> bool:
    r = returns.dropna()
    if len(r) < 4:
        return True
    sr = float(r.mean() / (r.std(ddof=1) + 1e-12))
    return moments_degenerate(sr, float(r.skew()), float(r.kurt()))

def compute_verdict(trial_return_matrix: pd.DataFrame,
                    regime_labels: pd.DataFrame,
                    calmar_overall: float) -> dict:
    notes: dict = {}
    K_eff = k_effective(trial_return_matrix)
    if trial_return_matrix.shape[1] < K_MIN:
        notes["insufficient_trials"] = True
        return {"pass": False, "watch": False, "shelf": True,
                "dsr": 0.0, "fwer": 1.0, "k_effective": K_eff,
                "regime_kill": False, "calmar_overall": calmar_overall,
                "notes": notes}
    best = _best_trial_returns(trial_return_matrix)
    if _is_degenerate(best):
        notes["degenerate_moments"] = True
        return {"pass": False, "watch": False, "shelf": True,
                "dsr": 0.0, "fwer": 1.0, "k_effective": K_eff,
                "regime_kill": False, "calmar_overall": calmar_overall,
                "notes": notes}
    dsr = deflated_sharpe(best, K_effective=K_eff)
    # alpha_K = 1 - (1 - alpha)^E[K], where alpha is this strategy's own observed
    # single-trial p-value. Passing a constant alpha here makes fwer_val a function
    # of K alone, and the gate a tautology no strategy can clear.
    fwer_val = fwer(K_eff, alpha=observed_alpha(best))
    reg_tbl = per_regime_sharpe(best, regime_labels)
    reg_killed = regime_kill(reg_tbl)
    is_pass = (dsr > DSR_PASS and fwer_val < FWER_PASS
               and not reg_killed and calmar_overall >= 1.0)
    # Watch is "alive but not promoted". Shelf is reserved for dead strategies,
    # so it must be a floor on DSR -- never a bucket a high-DSR strategy falls into.
    is_watch = (not is_pass) and dsr >= DSR_WATCH_LO
    is_shelf = not (is_pass or is_watch)
    return {"pass": is_pass, "watch": is_watch, "shelf": is_shelf,
            "dsr": dsr, "fwer": fwer_val, "k_effective": K_eff,
            "regime_kill": bool(reg_killed), "calmar_overall": calmar_overall,
            "notes": notes}

def persist_verdict(verdict: dict, run_dir: str) -> pathlib.Path:
    d = pathlib.Path(run_dir)
    if (d / "SEALED").exists():
        raise FileExistsError(f"{d}/SEALED already exists; refusing overwrite")
    d.mkdir(parents=True, exist_ok=True)
    vpath = d / "verdict.json"
    vpath.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    sealed = d / "SEALED"
    sealed.write_text("sealed\n", encoding="utf-8")
    ro = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH  # 444
    vpath.chmod(ro)
    sealed.chmod(ro)
    return d
