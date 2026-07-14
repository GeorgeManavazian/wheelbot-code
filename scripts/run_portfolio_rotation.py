"""Portfolio rotation A/B (pre-registered, spec 2026-07-14): rotated shared
pool vs the four solo basis wheels. Seen tickers only, EOD fills, raw.

Run: PYTHONPATH=. .venv/bin/python scripts/run_portfolio_rotation.py
"""
import pandas as pd
from pathlib import Path
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.wheel import WheelConfig, run_wheel
from src.engine_v2.options.portfolio import run_portfolio_wheel, DEFAULT_CLEAN_START
from src.engine_v2.options.report import wheel_report, spy_curve
from src.engine_v2.backtest import metrics_simple as m
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

SEEN = ["SPY", "GDX", "SLV", "XOP"]
BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0,
            call_min_strike="basis")

def perf(eq):
    ppy = m.infer_periods_per_year(eq.index)
    rets = eq.pct_change().fillna(0.0)
    return (float(eq.iloc[-1] / eq.iloc[0] - 1), m.cagr(eq, ppy),
            m.sharpe(rets, ppy), m.max_drawdown(eq))

def main():
    chains = {t: pd.read_parquet(chain_path(t)) for t in SEEN}
    states = {t: regime_series(closes_for(t)) for t in SEEN}
    lines = ["Portfolio rotation A/B — frozen config, EOD, raw.",
             f"clean starts honored: { {k: str(v.date()) for k, v in DEFAULT_CLEAN_START.items()} }"]

    lines.append(f"\n{'arm':<26} {'P&L':>10} {'total':>8} {'CAGR':>7} {'Sharpe':>7} "
                 f"{'maxDD':>7} {'flat':>5} {'uncov':>6}")
    solo_pnl = []
    for t in SEEN:
        ch = chains[t]
        if t in DEFAULT_CLEAN_START:   # same clean start for the solo baseline
            ch = ch[pd.to_datetime(ch["date"]) >= DEFAULT_CLEAN_START[t]]
        cfg = WheelConfig(ticker=t, **BASE)
        res = run_wheel(ch, cfg)
        rep = wheel_report(res, ch, cfg)
        tot, cagr, sh, dd = perf(res.equity)
        s = rep.stats
        solo_pnl.append(res.equity.iloc[-1] - cfg.starting_capital)
        lines.append(f"{'solo ' + t:<26} {res.equity.iloc[-1]-100_000:>10,.0f} "
                     f"{tot:>8.1%} {cagr:>7.1%} {sh:>7.2f} {dd:>7.1%} "
                     f"{s['n_days_flat']:>5} {s['days_shares_uncovered']:>6}")
    lines.append(f"{'solo equal-weight avg':<26} {sum(solo_pnl)/4:>10,.0f}")

    cfg = WheelConfig(ticker="SPY", **BASE)
    port = run_portfolio_wheel(chains, cfg, states)
    tot, cagr, sh, dd = perf(port.equity)
    per_ticker = {}
    for t_ in port.trades:
        if t_.action == "SELL_PUT":
            per_ticker[t_.contract.root] = per_ticker.get(t_.contract.root, 0) + 1
    lines.append(f"{'ROTATED portfolio':<26} {port.equity.iloc[-1]-100_000:>10,.0f} "
                 f"{tot:>8.1%} {cagr:>7.1%} {sh:>7.2f} {dd:>7.1%} "
                 f"{port.days_flat:>5} {port.days_shares_uncovered:>6}")
    lines.append(f"  entries by ticker: {per_ticker}  routes {len(port.route_events)}"
                 f"  state-unknown {sum(1 for w in port.warnings if w[1]=='route_state_unknown')}")

    spy = spy_curve(100_000.0, port.equity.index)
    if spy is not None:
        tot, cagr, sh, dd = perf(spy.dropna())
        lines.append(f"{'buy-hold SPY':<26} {spy.dropna().iloc[-1]-100_000:>10,.0f} "
                     f"{tot:>8.1%} {cagr:>7.1%} {sh:>7.2f} {dd:>7.1%}")
    txt = "\n".join(lines)
    Path("data/options/reports").mkdir(parents=True, exist_ok=True)
    Path("data/options/reports/portfolio_rotation.txt").write_text(txt)
    print(txt)

if __name__ == "__main__":
    main()
