"""Chop-scanner rotation A/B (spec 2026-07-17): chop-selected rotation at
N=1 and N=5 vs the solo equal-weight wheel (matched 0.50 call delta) vs
buy-hold, on the 9-ticker in-sample dev universe. Raw, EOD.

Run: PYTHONPATH=. .venv/bin/python scripts/run_portfolio_rotation.py
"""
import pandas as pd
from pathlib import Path
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.wheel import WheelConfig, run_wheel
from src.engine_v2.options.portfolio import (run_portfolio_wheel, ROTATION_TIE_ORDER,
                                             DEFAULT_CLEAN_START)
from src.engine_v2.options.report import wheel_report, buy_hold_curve
from src.engine_v2.backtest import metrics_simple as m
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

UNIVERSE = list(ROTATION_TIE_ORDER)   # the 9 dev tickers
BASE = dict(put_delta=0.20, call_delta=0.50, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0,
            call_min_strike="basis")


def perf(eq):
    ppy = m.infer_periods_per_year(eq.index)
    rets = eq.pct_change().fillna(0.0)
    return (float(eq.iloc[-1] / eq.iloc[0] - 1), m.cagr(eq, ppy),
            m.sharpe(rets, ppy), m.max_drawdown(eq))


def line(name, eq, extra=""):
    tot, cagr, sh, dd = perf(eq)
    return (f"{name:<28} {eq.iloc[-1]-100_000:>10,.0f} {tot:>8.1%} "
            f"{cagr:>7.1%} {sh:>7.2f} {dd:>7.1%}  {extra}")


def main():
    chains = {t: pd.read_parquet(chain_path(t)) for t in UNIVERSE}
    states = {t: regime_series(closes_for(t)) for t in UNIVERSE}
    lines = ["Chop-scanner rotation A/B — frozen config (0.20 put / 0.50 call / "
             "50% TP / DTE 7 / basis), EOD, raw.",
             f"universe: {UNIVERSE}",
             f"{'arm':<28} {'P&L':>10} {'total':>8} {'CAGR':>7} {'Sharpe':>7} {'maxDD':>7}"]

    # solo equal-weight baseline (matched 0.50 call delta)
    solo_pnl = []
    for t in UNIVERSE:
        ch = chains[t]
        if t in DEFAULT_CLEAN_START:
            ch = ch[pd.to_datetime(ch["date"]) >= DEFAULT_CLEAN_START[t]]
        cfg = WheelConfig(ticker=t, **BASE)
        res = run_wheel(ch, cfg)
        solo_pnl.append(res.equity.iloc[-1] - 100_000)
    lines.append(f"{'solo equal-weight (0.50 call)':<28} {sum(solo_pnl)/len(UNIVERSE):>10,.0f}"
                 f"  (avg of {len(UNIVERSE)} solo wheels)")

    cfg = WheelConfig(ticker="SPY", **BASE)
    for n in (1, 5):
        port = run_portfolio_wheel(chains, cfg, states, selector="chop", n_slots=n)
        entries = {}
        for tr in port.trades:
            if tr.action == "SELL_PUT":
                entries[tr.contract.root] = entries.get(tr.contract.root, 0) + 1
        lines.append(line(f"CHOP rotation N={n}", port.equity,
                          f"| campaigns {port.n_campaigns_opened}  flat {port.days_flat}"
                          f"  entries {entries}"))

    # buy-hold SPY reference (config-independent)
    bh = buy_hold_curve(chains["SPY"], 100_000.0)
    lines.append(line("buy-hold SPY", bh))

    txt = "\n".join(lines)
    Path("data/options/reports").mkdir(parents=True, exist_ok=True)
    Path("data/options/reports/chop_rotation.txt").write_text(txt)
    print(txt)


if __name__ == "__main__":
    main()
