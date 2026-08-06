"""Does IV-rank sorting starve its own candidate pool?

Established so far:
  * The IV sort loses 51 campaigns (254 -> 203) and 5 points of return.
  * NOT capital: at 100x capital the gap widens (-20.1% -> -21.7%).
  * NOT a code-level veto: pool membership is untouched and
    test_ranking_never_refuses_an_entry pins that a sort cannot refuse.
  * The loss is IDLE CAPACITY: mean slots occupied 4.02 vs 4.92, and 452 idle
    slot-days vs 38 -- worsening yearly (2026: 3.32 vs 4.97).

The remaining hypothesis: SELF-STARVATION. A slot can only be filled from
names not already held (`if tk in held_tickers: continue`). The weather gate
admits few names on a typical day, and IV-rank concentrates harder than
vol_pctile (54 unique tickers vs 66, top-5 share 42.9% vs 35.8%). If the sort
keeps choosing the same rich-IV names, then while those names are held the
remaining pool is thinner -- and the slot sits empty.

THE TEST: self-starvation scales with how many names are held at once. At
n_slots=1 only one name is excluded and starvation is near-zero; at n_slots=5
five are. So:

  * If the campaign gap SHRINKS toward zero as n_slots falls -> starvation
    confirmed. The sort is not intrinsically worse; it interacts badly with
    the held-exclusion rule at width, which is a fixable design question
    (e.g. allow re-entry, or widen the gate) rather than a verdict on IV rank.
  * If the gap PERSISTS at n_slots=1 -> starvation is not the mechanism. IV
    rank simply picks worse names inside the chop-selected pool, and the
    ranking variant is rejected on the same evidence the veto was.

Run: PYTHONPATH=. .venv/bin/python scratchpad/diag_iv_sort_slot_starvation.py
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
SLOTS = [1, 3, 5]


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

    out = {}
    hdr = "%-6s %-12s %11s %9s %8s %9s" % ("slots", "arm", "campaigns",
                                           "total", "Sharpe", "uniq_tk")
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
            tot = float(eq.iloc[-1] / eq.iloc[0] - 1)
            from src.engine_v2.backtest import metrics_simple as m
            ppy = m.infer_periods_per_year(eq.index)
            sh = m.sharpe(eq.pct_change().fillna(0.0), ppy)
            uniq = len({t.contract.root for t in res.trades
                        if t.action == "SELL_PUT"})
            out[(n, arm)] = res.n_campaigns_opened
            print("%-6d %-12s %11d %8.1f%% %8.2f %9d"
                  % (n, arm, res.n_campaigns_opened, tot * 100, sh, uniq),
                  flush=True)

    print("\n--- campaign gap vs n_slots ---")
    gaps = {}
    for n in SLOTS:
        b, s = out[(n, "vol_pctile")], out[(n, "iv_rank")]
        gaps[n] = 100.0 * (s - b) / b
        print("n_slots=%d: %d -> %d  (%+.1f%%)" % (n, b, s, gaps[n]))

    print()
    if abs(gaps[1]) < 5.0 <= abs(gaps[5]):
        print("SELF-STARVATION CONFIRMED. The gap vanishes at n_slots=1 and "
              "appears only at width, so IV rank is not picking worse names -- "
              "it collides with the held-exclusion rule. That is a design "
              "question about re-entry/pool width, not a verdict on the signal.")
    elif abs(gaps[1]) >= 5.0:
        print("NOT STARVATION. The gap persists at n_slots=1 (%+.1f%%), where "
              "only one name is ever excluded. IV rank is choosing worse names "
              "inside the chop-selected pool -- the ranking variant fails on "
              "the same evidence the veto did." % gaps[1])
    else:
        print("Inconclusive: gaps %+.1f%% / %+.1f%% / %+.1f%% at 1/3/5 slots."
              % (gaps[1], gaps[3], gaps[5]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
