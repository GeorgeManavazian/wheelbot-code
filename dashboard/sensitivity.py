"""Cost sensitivity: where does the edge die? Re-runs the backtest per spread level.

This is the only expensive panel in the dashboard — it runs the backtest once per
level, so the Run page puts it behind an explicit button, never on every run.
"""
import dataclasses
import pandas as pd

from src.engine_v2.backtest.orchestrator import BacktestConfig
from src.engine_v2.backtest.simple import run_simple

SPREAD_LEVELS: tuple = (0.0, 1.0, 2.0, 5.0, 10.0)


def cost_sweep(strategy_cls, bars, base_config: BacktestConfig,
               benchmark_bars=None, spreads=SPREAD_LEVELS) -> pd.DataFrame:
    rows = []
    for spread in spreads:
        cfg = dataclasses.replace(base_config, spread_bps_per_side=float(spread))
        res = run_simple(strategy_cls, bars, config=cfg,
                         benchmark_bars=benchmark_bars)
        rows.append({"spread_bps": float(spread),
                     "cagr": res.cagr,
                     "sharpe": res.sharpe})
    return pd.DataFrame(rows, columns=["spread_bps", "cagr", "sharpe"])
