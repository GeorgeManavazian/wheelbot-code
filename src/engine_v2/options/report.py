"""Reporting for the wheel backtest: headline + recent + year-by-year metrics,
a buy-hold SPY benchmark, and trade-log stats. Reuses the frequency-aware
metrics_simple. No verdict/gate — diagnostics only."""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from .wheel import underlying_series
from ..backtest import metrics_simple as m

@dataclass
class WheelReport:
    metrics: dict
    recent: dict
    yearly_return: pd.Series
    yearly_sharpe: pd.Series
    benchmark: dict
    stats: dict
    periods_per_year: float

def spy_buy_hold(chain, starting_capital) -> pd.Series:
    und = underlying_series(chain)
    return (starting_capital * und / und.iloc[0]).rename("spy_buy_hold")

def _perf(equity, ppy) -> dict:
    rets = equity.pct_change().fillna(0.0)
    return {
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1) if len(equity) else float("nan"),
        "cagr": m.cagr(equity, ppy),
        "sharpe": m.sharpe(rets, ppy),
        "max_drawdown": m.max_drawdown(equity),
    }

def wheel_stats(trades, cfg) -> dict:
    mult = cfg.contract_multiplier
    def of(a): return [t for t in trades if t.action == a]
    sells = of("SELL_PUT") + of("SELL_CALL")
    closes = of("CLOSE_PUT") + of("CLOSE_CALL")
    prem_in = sum(t.price_per_contract * mult * t.contracts for t in sells)
    prem_out = sum(t.price_per_contract * mult * t.contracts for t in closes)
    commission = cfg.commission_per_contract * sum(t.contracts for t in sells + closes)
    n_puts = len(of("SELL_PUT"))
    return {
        "n_puts_sold": n_puts, "n_calls_sold": len(of("SELL_CALL")),
        "n_assignments": len(of("ASSIGNED")), "n_called_away": len(of("CALLED_AWAY")),
        "n_take_profits": len(closes),
        "n_expired": len(of("PUT_EXPIRED")) + len(of("CALL_EXPIRED")),
        "premium_collected": prem_in, "premium_paid_to_close": prem_out,
        "commission_paid": commission,
        "net_premium": prem_in - prem_out - commission,
        "assignment_rate": (len(of("ASSIGNED")) / n_puts) if n_puts else 0.0,
    }

def wheel_report(result, chain, cfg, recent_start="2021-07-01") -> WheelReport:
    eq = result.equity
    ppy = m.infer_periods_per_year(eq.index)
    rs = max(pd.Timestamp(recent_start), eq.index.min())
    eq_recent = eq[eq.index >= rs]
    bh = spy_buy_hold(chain, cfg.starting_capital).reindex(eq.index).ffill()
    return WheelReport(
        metrics=_perf(eq, ppy),
        recent=_perf(eq_recent, ppy) if len(eq_recent) > 1 else _perf(eq, ppy),
        yearly_return=m.yearly_returns(eq),
        yearly_sharpe=m.yearly_sharpe(eq.pct_change().fillna(0.0), ppy),
        benchmark=_perf(bh, ppy),
        stats=wheel_stats(result.trades, cfg),
        periods_per_year=ppy,
    )
