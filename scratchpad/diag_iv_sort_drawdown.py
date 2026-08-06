"""Max drawdown for the IV-rank SORT at n_slots 1 and 3 (5 already measured).

diag_iv_sort_slot_starvation.py printed campaigns / total / Sharpe but not
maxDD, and drawdown is the axis that matters most for reading the n_slots=1
result: that arm returned +95.4% vs +88.1% baseline, but with ~4x the position
size (median 12 contracts vs 3). Concentration that buys return while widening
drawdown is a leverage dial, not an edge -- the same trap the original sweep
fell into by sorting arms on total return.

Known already (n_slots=5): baseline -15.5% maxDD, iv_rank -18.9%.

Run: PYTHONPATH=. .venv/bin/python scratchpad/diag_iv_sort_drawdown.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from src.engine_v2.backtest import metrics_simple as m
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.iv_rank import IVHistory
from src.engine_v2.options.portfolio import run_portfolio_wheel
from src.engine_v2.options.report import buy_hold_curve
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.regime.data import closes_for
from src.engine_v2.regime.state import regime_series

UNIVERSE_FILE = Path("fixtures/iv_rank_ab_universe.json")
START = pd.Timestamp("2024-01-15")
END = pd.Timestamp("2026-07-01")
BASE = dict(put_delta=0.40, call_delta=0.50, target_dte=7,
            take_profit_pct=0.25, starting_capital=100_000.0,
            call_min_strike="basis",
            min_ann_yield_on_collateral=0.08,
            max_credit_pct_of_strike=0.03)
SLOTS = [1, 3]


def load():
    universe = json.loads(UNIVERSE_FILE.read_text())
    cfg0 = WheelConfig(ticker="SPY", **BASE)
    chains, states, series = {}, {}, {}
    for t in universe:
        try:
            full = pd.read_parquet(chain_path(t))
        except Exception:
            continue
        full["date"] = pd.to_datetime(full["date"])
        ch = full[(full["date"] >= START) & (full["date"] <= END)]
        if ch.empty:
            continue
        try:
            st = regime_series(closes_for(t))
        except Exception:
            continue
        one = IVHistory.from_chains({t: full}, cfg0)
        if one.known(t):
            series[t] = one._by[t]
        chains[t], states[t] = ch, st
        del full
    return chains, states, IVHistory(series)


def main():
    chains, states, hist = load()
    print("loaded %d tickers\n" % len(chains), flush=True)

    hdr = "%-6s %-12s %11s %9s %8s %8s %9s" % (
        "slots", "arm", "campaigns", "total", "Sharpe", "maxDD", "ret/DD")
    print(hdr)
    print("-" * len(hdr))
    for n in SLOTS:
        for arm in ("vol_pctile", "iv_rank"):
            cfg = WheelConfig(ticker="SPY", **BASE, rank_by=arm)
            res = run_portfolio_wheel(
                chains, cfg, states, selector="chop", n_slots=n,
                universe=sorted(chains),
                iv_history=hist if arm == "iv_rank" else None)
            eq = res.equity
            ppy = m.infer_periods_per_year(eq.index)
            tot = float(eq.iloc[-1] / eq.iloc[0] - 1)
            sh = m.sharpe(eq.pct_change().fillna(0.0), ppy)
            dd = m.max_drawdown(eq)
            print("%-6d %-12s %11d %8.1f%% %8.2f %7.1f%% %9.2f"
                  % (n, arm, res.n_campaigns_opened, tot * 100, sh, dd * 100,
                     tot / abs(dd) if dd else float("nan")), flush=True)

    bh = buy_hold_curve(chains["SPY"], 100_000.0) if "SPY" in chains else None
    if bh is not None:
        ppy = m.infer_periods_per_year(bh.index)
        tot = float(bh.iloc[-1] / bh.iloc[0] - 1)
        print("\n%-6s %-12s %11s %8.1f%% %8.2f %7.1f%% %9.2f"
              % ("-", "buy-hold SPY", "-", tot * 100,
                 m.sharpe(bh.pct_change().fillna(0.0), ppy),
                 m.max_drawdown(bh) * 100,
                 tot / abs(m.max_drawdown(bh))))
    print("\n(n_slots=5 measured earlier: baseline -15.5%%, iv_rank -18.9%%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
