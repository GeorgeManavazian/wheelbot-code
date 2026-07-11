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
    benchmark_recent: dict
    stats: dict
    periods_per_year: float
    recent_is_full: bool = False

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
    recent_is_full = rs <= eq.index.min()
    bh_recent = bh[bh.index >= rs]
    benchmark_recent = _perf(bh_recent, ppy) if len(bh_recent) > 1 else _perf(bh, ppy)
    return WheelReport(
        metrics=_perf(eq, ppy),
        recent=_perf(eq_recent, ppy) if len(eq_recent) > 1 else _perf(eq, ppy),
        yearly_return=m.yearly_returns(eq),
        yearly_sharpe=m.yearly_sharpe(eq.pct_change().fillna(0.0), ppy),
        benchmark=_perf(bh, ppy),
        benchmark_recent=benchmark_recent,
        recent_is_full=recent_is_full,
        stats=wheel_stats(result.trades, cfg),
        periods_per_year=ppy,
    )

def _pct(x): return "n/a" if pd.isna(x) else f"{x:+.2%}"
def _num(x): return "n/a" if pd.isna(x) else f"{x:.2f}"

def format_report(rep) -> str:
    L = []
    L.append("WHEEL BACKTEST REPORT")
    L.append("=" * 40)
    def block(title, d):
        L.append(f"\n{title}")
        L.append(f"  CAGR {_pct(d['cagr'])}   Sharpe {_num(d['sharpe'])}   "
                 f"Max drawdown {_pct(d['max_drawdown'])}   Total {_pct(d['total_return'])}")
    block("Headline (recent window)", rep.recent)
    block("  vs SPY buy-hold (recent window)", rep.benchmark_recent)
    if rep.recent_is_full:
        L.append("  (recent window = full history — span too short to separate)")
    block("Full history", rep.metrics)
    block("Benchmark — SPY buy-hold", rep.benchmark)
    L.append("\nYear-by-year (return / Sharpe)")
    for y in rep.yearly_return.index:
        L.append(f"  {y}: {_pct(rep.yearly_return[y])}  /  Sharpe {_num(rep.yearly_sharpe.get(y, float('nan')))}")
    s = rep.stats
    L.append("\nWheel stats")
    L.append(f"  puts sold {s['n_puts_sold']}  calls sold {s['n_calls_sold']}  "
             f"assignments {s['n_assignments']} (rate {s['assignment_rate']:.0%})  "
             f"called away {s['n_called_away']}  take-profits {s['n_take_profits']}")
    L.append(f"  premium collected {s['premium_collected']:.0f}  "
             f"paid to close {s['premium_paid_to_close']:.0f}  "
             f"commission {s['commission_paid']:.0f}  net {s['net_premium']:.0f}")
    return "\n".join(L)
