"""Rebuild a strategy from a leaderboard row and re-run its backtest.

Nothing here imports Streamlit — pure and unit-testable. Caching lives in
dashboard/shared.py. The stale check is an honesty guardrail: the dashboard
must never display numbers the CURRENT engine wouldn't produce.
"""
import math

from src.engine.backtest import run_backtest
from src.engine.metrics import summarize
from src.strategies.momentum_rotation import MomentumRotation
from src.strategies.ts_trend import TSTrend

STRATEGIES = {cls.name: cls for cls in (TSTrend, MomentumRotation)}
CHECK_METRICS = ("sharpe", "cagr", "max_dd")
RTOL = 1e-6  # spec: stale-leaderboard tolerance


class UnknownStrategyError(Exception):
    pass


def row_to_strategy(row: dict):
    cls = STRATEGIES.get(row["name"])
    if cls is None:
        raise UnknownStrategyError(
            f"strategy '{row['name']}' is not in src/strategies/ — "
            "renamed or deleted since this batch ran? Leaderboard row still "
            "counts; detail view is unavailable.")
    params = {}
    for key, default in cls.DEFAULTS.items():
        val = row.get(key, default)
        if val is None or (isinstance(val, float) and math.isnan(val)):
            val = default
        params[key] = type(default)(val)  # CSV floats -> declared param type
    return cls(**params)


def recompute_run(row: dict, long_df):
    return run_backtest(long_df, row_to_strategy(row))


def check_stale(row: dict, result) -> list[str]:
    """[] = leaderboard matches current engine; else mismatch descriptions."""
    stats = summarize(result)
    problems = []
    for m in CHECK_METRICS:
        want, got = float(row[m]), float(stats[m])
        if math.isnan(want) and math.isnan(got):
            continue
        if not math.isclose(want, got, rel_tol=RTOL, abs_tol=1e-9):
            problems.append(
                f"{m}: leaderboard {want:.6f} vs recomputed {got:.6f}")
    return problems
