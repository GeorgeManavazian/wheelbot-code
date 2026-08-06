"""Is the n_slots=1 baseline (+88.1%) built on fillable trades?

n_slots=1 puts the ENTIRE $100k behind one short put, so positions are ~4x the
size of the 5-slot arm ($25,439 median collateral). Every backtest here runs
with the A2 liquidity gate OFF -- it has to, because backtest chains carry no
open_interest/volume columns (a declared divergence, see WheelConfig).

That matters because the live audit found an ungated bot requesting 757 DOW
contracts against ~84/day actually traded, and the live gate would have refused
114 of 145 real entries as fantasy fills. The most concentrated arm is the most
exposed to that, and it is the one printing the biggest number.

This prints position SIZE for n_slots 1 vs 5 so the +88.1% can be judged on
whether those fills could plausibly exist -- not on the return column.

Run: PYTHONPATH=. .venv/bin/python scratchpad/diag_slot1_fill_realism.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from src.engine_v2.options.data import chain_path
from src.engine_v2.options.iv_rank import IVHistory
from src.engine_v2.options.portfolio import run_portfolio_wheel
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
MULT = 100


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
    chains, states, _ = load()
    print("loaded %d tickers\n" % len(chains), flush=True)

    rows = []
    for n in (1, 5):
        cfg = WheelConfig(ticker="SPY", **BASE, rank_by="vol_pctile")
        res = run_portfolio_wheel(chains, cfg, states, selector="chop",
                                  n_slots=n, universe=sorted(chains))
        eq = res.equity
        tot = float(eq.iloc[-1] / eq.iloc[0] - 1)
        print("n_slots={}  final equity = {:,.0f}  total = {:+.1f}%  "
              "campaigns={}".format(n, eq.iloc[-1], tot * 100,
                                    res.n_campaigns_opened), flush=True)
        for t in res.trades:
            if t.action != "SELL_PUT":
                continue
            rows.append(dict(n_slots=n, date=t.date, root=t.contract.root,
                             strike=float(t.contract.strike),
                             contracts=int(t.contracts),
                             credit=float(t.price_per_contract)))
    df = pd.DataFrame(rows)
    df["collateral"] = df.strike * MULT * df.contracts
    df.to_parquet("scratchpad/slot1_fill_realism.parquet")

    print("\n--- position SIZE per entry ---")
    print(df.groupby("n_slots")[["contracts", "collateral"]]
            .agg(["mean", "median", "max"]).round(0).to_string())

    print("\n--- contracts per entry, distribution ---")
    for n in (1, 5):
        c = df[df.n_slots == n].contracts
        print("n_slots=%d  p50=%d  p90=%d  p99=%d  max=%d  |  %%>50 contracts = %.0f%%"
              % (n, c.quantile(.50), c.quantile(.90), c.quantile(.99), c.max(),
                 100.0 * (c > 50).mean()))

    print("\n--- the biggest single entries at n_slots=1 ---")
    top = df[df.n_slots == 1].nlargest(10, "contracts")[
        ["date", "root", "strike", "contracts", "collateral"]]
    print(top.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
