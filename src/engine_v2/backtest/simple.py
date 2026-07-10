"""Clean backtest seam: one straight pass over the full date range, no CPCV,
no gate, no verdict. Returns rich diagnostics. This is the workbench entrypoint."""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from ..strategy.protocol import validate_plugin
from .orchestrator import position_history, BacktestConfig
from . import metrics_simple as m

@dataclass
class Result:
    equity: pd.Series
    returns: pd.Series
    trades: int
    cagr: float
    sharpe: float
    max_drawdown: float
    yearly: pd.Series
    yearly_sharpe: pd.Series
    recent: dict
    regime: pd.DataFrame
    benchmarks: dict
    periods_per_year: float

def run_simple(strategy_cls, bars: pd.DataFrame,
               config: BacktestConfig | None = None,
               params: dict | None = None,
               periods_per_year: float | None = None,
               recent_start: str = "2021-07-01") -> Result:
    validate_plugin(strategy_cls)
    cfg = config or BacktestConfig()
    ppy = periods_per_year or m.infer_periods_per_year(bars.index)

    hist = position_history(strategy_cls, params or {}, bars, bars.index, cfg, ppy)
    equity = hist["equity"]
    returns = equity.pct_change().fillna(0.0)

    # Standing methodology: recent window is the HEADLINE; keep full history too.
    eq_recent = equity[equity.index >= recent_start]
    ret_recent = returns[returns.index >= recent_start]
    recent = {
        "start": recent_start,
        "cagr": m.cagr(eq_recent, ppy),
        "sharpe": m.sharpe(ret_recent, ppy),
        "max_drawdown": m.max_drawdown(eq_recent),
    }

    benchmarks = {"SPY": m.buy_hold_equity(bars, {"SPY": 1.0}, cfg.starting_equity)}
    if "TLT" in set(bars.columns.get_level_values(0)):
        benchmarks["60_40"] = m.buy_hold_equity(
            bars, {"SPY": 0.6, "TLT": 0.4}, cfg.starting_equity)

    return Result(
        equity=equity,
        returns=returns,
        trades=int((hist["traded"] > 0).sum()),
        cagr=m.cagr(equity, ppy),
        sharpe=m.sharpe(returns, ppy),
        max_drawdown=m.max_drawdown(equity),
        yearly=m.yearly_returns(equity),
        yearly_sharpe=m.yearly_sharpe(returns, ppy),
        recent=recent,
        regime=m.regime_breakdown(returns, bars, ppy),
        benchmarks=benchmarks,
        periods_per_year=ppy,
    )
