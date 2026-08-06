"""Is the IV-sort campaign drop a FILTER, or a consequence of lost capital?

run_iv_rank_sort_ab.py tripped its tripwire: campaigns 254 -> 203. The three
obvious mechanisms were measured (diag_iv_sort_campaign_drop.py) and all point
the WRONG way -- the IV arm locks less collateral per entry ($22,952 vs
$25,439), holds no longer (15.8 vs 15.5 days mean), and is assigned less
(18.7% vs 20.1%). Each of those should permit MORE campaigns, not 51 fewer.

The per-quarter entry counts say what is actually happening:

    2024Q1  49/46   2024Q2 30/30   2024Q3 14/14   2024Q4 24/24   <- matched
    2025Q1  17/24   2025Q3  6/14   2025Q4 10/22   2026Q1 21/40   <- diverging

Identical for a year, then a widening gap. That is compounding equity
divergence (worse arm -> less capital -> fewer affordable entries -> further
behind), not an entry gate, which would bite uniformly from day one.

THE CONTROLLED TEST: run both arms with capital so large it never binds. If
the campaign counts converge, the drop was capital, the sort is not filtering,
and the tripwire premise ("a sort cannot change how many") was simply wrong --
it holds only when capital is unconstrained. If the gap SURVIVES at 10x
capital, something really is filtering and the A/B result is void.

Also saves both equity curves at the real 100k so the divergence date is
readable rather than inferred.

Run: PYTHONPATH=. .venv/bin/python scratchpad/diag_iv_sort_capital_control.py
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
EQ_OUT = Path("scratchpad/iv_sort_equity.parquet")

START = pd.Timestamp("2024-01-15")
END = pd.Timestamp("2026-07-01")
BASE = dict(put_delta=0.40, call_delta=0.50, target_dte=7,
            take_profit_pct=0.25, call_min_strike="basis",
            min_ann_yield_on_collateral=0.08,
            max_credit_pct_of_strike=0.03)
N_SLOTS = 5

# 100k is the real config. 10M is the control: 5 slots against a universe whose
# median entry locks ~$23k cannot exhaust it, so affordability never decides.
CAPITALS = [100_000.0, 10_000_000.0]


def load():
    universe = json.loads(UNIVERSE_FILE.read_text())
    cfg0 = WheelConfig(ticker="SPY", starting_capital=100_000.0, **BASE)
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

    res_by = {}
    eq_cols = {}
    print("%-12s %10s %11s %10s" % ("arm", "capital", "campaigns", "total"))
    print("-" * 46)
    for cap in CAPITALS:
        for arm in ("vol_pctile", "iv_rank"):
            cfg = WheelConfig(ticker="SPY", starting_capital=cap, **BASE,
                              rank_by=arm)
            res = run_portfolio_wheel(
                chains, cfg, states, selector="chop", n_slots=N_SLOTS,
                universe=sorted(chains),
                iv_history=hist if arm == "iv_rank" else None)
            tot = float(res.equity.iloc[-1] / res.equity.iloc[0] - 1)
            res_by[(cap, arm)] = res.n_campaigns_opened
            if cap == 100_000.0:
                eq_cols[arm] = res.equity
            print("{:<12} {:>10,.0f} {:>11d} {:>8.1f}%".format(
                arm, cap, res.n_campaigns_opened, tot * 100), flush=True)

    pd.DataFrame(eq_cols).to_parquet(EQ_OUT)
    print("\nwrote %s" % EQ_OUT, flush=True)

    print("\n--- verdict ---")
    for cap in CAPITALS:
        b, s = res_by[(cap, "vol_pctile")], res_by[(cap, "iv_rank")]
        print("capital {:,.0f}: {} -> {}  ({:+d}, {:+.1f}%)".format(
            cap, b, s, s - b, 100.0 * (s - b) / b))

    lo_gap = abs(res_by[(CAPITALS[0], "iv_rank")]
                 - res_by[(CAPITALS[0], "vol_pctile")]) / res_by[(CAPITALS[0], "vol_pctile")]
    hi_gap = abs(res_by[(CAPITALS[1], "iv_rank")]
                 - res_by[(CAPITALS[1], "vol_pctile")]) / res_by[(CAPITALS[1], "vol_pctile")]
    print()
    if hi_gap < 0.05 <= lo_gap:
        print("CAPITAL. The gap closes once affordability stops binding, so the "
              "sort is NOT filtering -- the campaign drop is a downstream "
              "effect of the arm earning less. The A/B result stands and the "
              "tripwire premise was too strong.")
    elif hi_gap >= 0.05:
        print("FILTER. The gap SURVIVES unconstrained capital (%.1f%%), so the "
              "sort is removing entries, not reordering them. The A/B return "
              "column is measuring a contaminated arm -- do not read it."
              % (hi_gap * 100))
    else:
        print("Inconclusive: gaps %.1f%% (100k) vs %.1f%% (10M)."
              % (lo_gap * 100, hi_gap * 100))

    eq = pd.DataFrame(eq_cols)
    rel = eq["iv_rank"] / eq["vol_pctile"] - 1.0
    first = rel[rel.abs() > 0.005].index.min() if (rel.abs() > 0.005).any() else None
    print("\nequity curves first diverge >0.5%% on: %s" % first)
    print("gap at 2024-12-31: %+.2f%%"
          % (100 * rel[rel.index <= pd.Timestamp("2024-12-31")].iloc[-1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
