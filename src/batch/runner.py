"""Batch runner: strategy x parameter grid -> leaderboard with honesty stats.

One crashing strategy never kills the batch — it gets an error row.
The luck warning is printed with every leaderboard: after N tries on the
same data, the best Sharpe you see is inflated by selection alone.
"""
import itertools
import math
import traceback

import pandas as pd

from src.engine.backtest import run_backtest
from src.engine.metrics import summarize


METRIC_COLS = ["cagr", "max_dd", "sharpe", "n_trades", "turnover",
               "exposure", "positive_years", "total_years",
               "best_year", "worst_year", "top2_share", "sample_flag"]


def expand_grid(cls, grid: dict) -> list:
    keys = sorted(grid)
    return [cls(**dict(zip(keys, combo)))
            for combo in itertools.product(*(grid[k] for k in keys))]


def run_batch(strategies: list, long_df: pd.DataFrame, config=None,
              out_csv=None) -> pd.DataFrame:
    param_cols = sorted({k for s in strategies for k in s.params})
    columns = ["label", "name", *param_cols, *METRIC_COLS, "error"]
    rows = []
    for i, strat in enumerate(strategies, 1):
        row = {"label": strat.label(), "name": strat.name, **strat.params,
               "error": ""}
        try:
            row.update(summarize(run_backtest(long_df, strat, config)))
            print(f"[{i}/{len(strategies)}] {strat.label()} "
                  f"sharpe={row['sharpe']:.2f} cagr={row['cagr']:.1%}")
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"
            print(f"[{i}/{len(strategies)}] {strat.label()} ERROR: {row['error']}")
            traceback.print_exc()
        rows.append(row)
        if out_csv:
            pd.DataFrame([row]).reindex(columns=columns).to_csv(
                out_csv, mode="w" if i == 1 else "a", header=(i == 1),
                index=False)
    lb = pd.DataFrame(rows).reindex(columns=columns)
    lb = lb.sort_values("sharpe", ascending=False, na_position="last")
    return lb.reset_index(drop=True)


def luck_warning(n_runs: int, years: float) -> str:
    exp_max = math.sqrt(2 * math.log(max(n_runs, 2)) / years)
    return (f"MULTIPLE-TESTING WARNING: {n_runs} runs on {years:.0f}y of data -> "
            f"best-by-pure-luck Sharpe ~{exp_max:.2f} — results below that "
            f"line are indistinguishable from noise.")


def plateau_table(leaderboard: pd.DataFrame, name: str, param: str,
                  metric: str = "sharpe") -> pd.DataFrame:
    """Pivot metric over one param; other params become rows.
    A robust edge shows a PLATEAU across columns; a spike = curve fit."""
    fam = leaderboard[leaderboard["name"] == name].copy()
    fam = fam.dropna(axis=1, how="all")  # other families' param cols are all-NaN here
    other = [c for c in fam.columns
             if c not in {param, metric, "label", "name", "error"}
             and c in _param_cols(fam)]
    index = other if other else None
    if index is None:
        fam["_"] = "all"
        index = ["_"]
    return fam.pivot_table(index=index, columns=param, values=metric)


def _param_cols(fam: pd.DataFrame) -> set:
    return {c for c in fam.columns
            if c not in set(METRIC_COLS) | {"label", "name", "error"}}
