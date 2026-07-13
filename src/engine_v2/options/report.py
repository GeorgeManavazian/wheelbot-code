"""Reporting for the wheel backtest: headline + recent + year-by-year metrics,
buy-hold benchmarks (the chain's own underlying, plus real SPY when its data is
on disk), and trade-log stats. Reuses the frequency-aware metrics_simple.
No verdict/gate — diagnostics only."""
from __future__ import annotations
import os
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
    benchmark_underlying: dict
    benchmark_underlying_recent: dict
    benchmark_spy: dict | None
    stats: dict
    periods_per_year: float
    ticker: str = "SPY"
    recent_is_full: bool = False

def buy_hold_curve(chain, starting_capital) -> pd.Series:
    """Buy-hold the chain's OWN underlying (was spy_buy_hold, which silently
    benchmarked GDX against GDX on non-SPY chains)."""
    und = underlying_series(chain)
    return (starting_capital * und / und.iloc[0]).rename("buy_hold")

def spy_curve(starting_capital, index, path="data/options/spy_greeks_eod_all.parquet"):
    """Buy-hold real SPY, reindex-ffilled to `index` — the honest cross-ticker
    benchmark. None if the SPY file is missing or the dates don't overlap."""
    if not os.path.exists(path):
        return None
    und = pd.read_parquet(path, columns=["date", "underlying"]).groupby("date")["underlying"].first()
    und = und.reindex(index).ffill().dropna()
    if und.empty:
        return None
    return (starting_capital * und / und.iloc[0]).rename("spy_buy_hold")

def _perf(equity, ppy) -> dict:
    rets = equity.pct_change().fillna(0.0)
    return {
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1) if len(equity) else float("nan"),
        "cagr": m.cagr(equity, ppy),
        "sharpe": m.sharpe(rets, ppy),
        "max_drawdown": m.max_drawdown(equity),
    }

def wheel_stats(result, cfg) -> dict:
    trades = result.trades
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
        "realized_dte": pd.Series(
            [(pd.Timestamp(t.contract.expiry) - pd.Timestamp(t.date)).days for t in sells],
            dtype=int).value_counts().sort_index(),
        "n_days_flat": result.days_flat,
        "pct_days_flat": (result.days_flat / len(result.equity)) if len(result.equity) else 0.0,
    }

def wheel_report(result, chain, cfg, recent_start="2021-07-01",
                 spy_path="data/options/spy_greeks_eod_all.parquet") -> WheelReport:
    eq = result.equity
    ppy = m.infer_periods_per_year(eq.index)
    rs = max(pd.Timestamp(recent_start), eq.index.min())
    eq_recent = eq[eq.index >= rs]
    bh = buy_hold_curve(chain, cfg.starting_capital).reindex(eq.index).ffill()
    spy = spy_curve(cfg.starting_capital, eq.index, path=spy_path)
    recent_is_full = rs <= eq.index.min()
    bh_recent = bh[bh.index >= rs]
    benchmark_recent = _perf(bh_recent, ppy) if len(bh_recent) > 1 else _perf(bh, ppy)
    return WheelReport(
        metrics=_perf(eq, ppy),
        recent=_perf(eq_recent, ppy) if len(eq_recent) > 1 else _perf(eq, ppy),
        yearly_return=m.yearly_returns(eq),
        yearly_sharpe=m.yearly_sharpe(eq.pct_change().fillna(0.0), ppy),
        benchmark_underlying=_perf(bh, ppy),
        benchmark_underlying_recent=benchmark_recent,
        benchmark_spy=_perf(spy, ppy) if spy is not None else None,
        recent_is_full=recent_is_full,
        stats=wheel_stats(result, cfg),
        periods_per_year=ppy,
        ticker=cfg.ticker,
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
    block(f"  vs buy-hold {rep.ticker} (recent window)", rep.benchmark_underlying_recent)
    if rep.recent_is_full:
        L.append("  (recent window = full history — span too short to separate)")
    block("Full history", rep.metrics)
    block(f"Benchmark — buy-hold {rep.ticker}", rep.benchmark_underlying)
    if rep.benchmark_spy is not None:
        block("Benchmark — buy-hold SPY", rep.benchmark_spy)
    L.append("\nYear-by-year (return / Sharpe)")
    for y in rep.yearly_return.index:
        L.append(f"  {y}: {_pct(rep.yearly_return[y])}  /  Sharpe {_num(rep.yearly_sharpe.get(y, float('nan')))}")
    s = rep.stats
    L.append("\nWheel stats")
    L.append(f"  puts sold {s['n_puts_sold']}  calls sold {s['n_calls_sold']}  "
             f"assignments {s['n_assignments']} (rate {s['assignment_rate']:.0%})  "
             f"called away {s['n_called_away']}  take-profits {s['n_take_profits']}  "
             f"flat {s['pct_days_flat']:.0%} of days")
    L.append(f"  premium collected {s['premium_collected']:.0f}  "
             f"paid to close {s['premium_paid_to_close']:.0f}  "
             f"commission {s['commission_paid']:.0f}  net {s['net_premium']:.0f}")
    dtes = s["realized_dte"]
    if len(dtes):
        exp = dtes.index.repeat(dtes.values).to_series()
        L.append(f"  realized DTE: {dtes.index.min()}..{dtes.index.max()}  "
                 f"(median {exp.median():.0f})")
    return "\n".join(L)

_RIGHT_WORD = {"P": "PUT", "C": "CALL"}
_TERM = {"CLOSE_PUT", "CLOSE_CALL", "PUT_EXPIRED", "CALL_EXPIRED", "ASSIGNED",
         "CALLED_AWAY", "ROLL_CLOSE"}

def position_log(result, cfg) -> pd.DataFrame:
    mult, comm = cfg.contract_multiplier, cfg.commission_per_contract
    rows, open_opt, assign = [], None, None
    for t in result.trades:
        if t.action in ("SELL_PUT", "SELL_CALL"):
            open_opt = t
        elif t.action in _TERM and open_opt is not None:
            oc, n = open_opt.contract, open_opt.contracts
            credit = open_opt.price_per_contract * mult * n - comm * n
            if t.action in ("CLOSE_PUT", "CLOSE_CALL"):
                cost, outcome = t.price_per_contract * mult * n + comm * n, "Took profit"
            elif t.action == "ROLL_CLOSE":
                cost, outcome = t.price_per_contract * mult * n + comm * n, "Rolled"
            elif t.action in ("PUT_EXPIRED", "CALL_EXPIRED"):
                cost, outcome = 0.0, "Expired worthless"
            elif t.action == "ASSIGNED":
                cost, outcome = 0.0, "Assigned"
                assign = (oc.strike, n, t.date)
            else:  # CALLED_AWAY
                cost, outcome = 0.0, "Called away"
            realized = credit - cost
            rows.append(dict(opened=open_opt.date, closed=t.date,
                instrument=_RIGHT_WORD[oc.right], strike=oc.strike, expiry=oc.expiry,
                qty=n, credit=credit, outcome=outcome, cost_to_close=cost,
                realized_pnl=realized,
                pct_of_credit=(realized / credit if credit else 0.0),
                days_held=(t.date - open_opt.date).days))
            if t.action == "CALLED_AWAY" and assign is not None:
                astrike, aqty, adate = assign
                rows.append(dict(opened=adate, closed=t.date, instrument="SHARES",
                    strike=astrike, expiry=pd.NaT, qty=aqty,
                    credit=-astrike * mult * aqty, outcome="Called away",
                    cost_to_close=oc.strike * mult * aqty,
                    realized_pnl=(oc.strike - astrike) * mult * aqty,
                    pct_of_credit=float("nan"), days_held=(t.date - adate).days))
                assign = None
            open_opt = None
        elif t.action == "LIQUIDATE" and assign is not None:
            # shares dumped at spot the moment assignment fired (LIQUIDATE is a
            # shares closure, not an option terminal — the put row was already
            # written by the ASSIGNED trade).
            astrike, aqty, adate = assign
            spot = t.price_per_contract
            rows.append(dict(opened=adate, closed=t.date, instrument="SHARES",
                strike=astrike, expiry=pd.NaT, qty=aqty,
                credit=-astrike * mult * aqty, outcome="Liquidated",
                cost_to_close=spot * mult * aqty,
                realized_pnl=(spot - astrike) * mult * aqty,
                pct_of_credit=float("nan"), days_held=(t.date - adate).days))
            assign = None
    if open_opt is not None:
        oc, n = open_opt.contract, open_opt.contracts
        credit = open_opt.price_per_contract * mult * n - comm * n
        prior = sum(r["realized_pnl"] for r in rows if pd.notna(r["realized_pnl"]))
        realized = (result.final_cash - cfg.starting_capital) - prior
        rows.append(dict(opened=open_opt.date, closed=pd.NaT,
            instrument=_RIGHT_WORD[oc.right], strike=oc.strike, expiry=oc.expiry, qty=n,
            credit=credit, outcome="Settled at mark", cost_to_close=credit - realized,
            realized_pnl=realized, pct_of_credit=(realized / credit if credit else 0.0),
            days_held=float("nan")))
        open_opt = None
    if assign is not None and getattr(result, "final_shares", 0) > 0:
        astrike, aqty, adate = assign
        rows.append(dict(opened=adate, closed=pd.NaT, instrument="SHARES",
            strike=astrike, expiry=pd.NaT, qty=aqty, credit=-astrike * mult * aqty,
            outcome="Open", cost_to_close=float("nan"), realized_pnl=float("nan"),
            pct_of_credit=float("nan"), days_held=float("nan")))
    cols = ["opened","closed","instrument","strike","expiry","qty","credit","outcome",
            "cost_to_close","realized_pnl","pct_of_credit","days_held"]
    return pd.DataFrame(rows, columns=cols)
