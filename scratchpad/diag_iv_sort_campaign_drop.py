"""Why did the IV-rank SORT lose 51 campaigns (254 -> 203)?

run_iv_rank_sort_ab.py tripped its own tripwire. The premise behind that
tripwire -- "a sort changes WHICH names fill the slots, never HOW MANY get
filled" -- is what has to be checked now, because the run says otherwise.

Three candidate mechanisms, all measurable from the trade log:

  1. COLLATERAL. Slots are capital, not counters. A richer-IV name with a
     higher strike locks more cash per contract, so the same budget buys fewer
     positions and some slots go unfilled. Sort changes the names, names carry
     different strikes -> count is NOT conserved.
  2. DURATION. A slot is occupied until the campaign closes. If IV-ranked names
     take longer to reach the 25% take-profit, each slot turns over less often
     and fewer campaigns start in the same window.
  3. ASSIGNMENT. An assigned put becomes shares -> covered calls -> the basis
     floor, which holds a slot far longer than a put that closes at TP.

Prints the per-arm evidence for each. Whichever moves is the explanation, and
it decides whether the tripwire caught a defect or an incorrect premise.

Run: PYTHONPATH=. .venv/bin/python scratchpad/diag_iv_sort_campaign_drop.py
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
OUT = Path("scratchpad/iv_sort_diag_trades.parquet")

START = pd.Timestamp("2024-01-15")
END = pd.Timestamp("2026-07-01")
BASE = dict(put_delta=0.40, call_delta=0.50, target_dte=7,
            take_profit_pct=0.25, starting_capital=100_000.0,
            call_min_strike="basis",
            min_ann_yield_on_collateral=0.08,
            max_credit_pct_of_strike=0.03)
N_SLOTS = 5
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


def to_frame(res, arm):
    rows = [dict(arm=arm, date=t.date, action=t.action, root=t.contract.root,
                 strike=float(t.contract.strike), expiry=t.contract.expiry,
                 contracts=int(t.contracts), campaign_id=int(t.campaign_id),
                 price=float(t.price_per_contract))
            for t in res.trades]
    return pd.DataFrame(rows)


def main():
    chains, states, hist = load()
    print("loaded %d tickers\n" % len(chains), flush=True)

    frames, eq = [], {}
    for arm in ("vol_pctile", "iv_rank"):
        cfg = WheelConfig(ticker="SPY", **BASE, rank_by=arm)
        res = run_portfolio_wheel(
            chains, cfg, states, selector="chop", n_slots=N_SLOTS,
            universe=sorted(chains),
            iv_history=hist if arm == "iv_rank" else None)
        frames.append(to_frame(res, arm))
        eq[arm] = res.equity
        print("%-12s campaigns=%d trades=%d"
              % (arm, res.n_campaigns_opened, len(res.trades)), flush=True)
    df = pd.concat(frames, ignore_index=True)
    df.to_parquet(OUT)
    print("\nwrote %s\n" % OUT, flush=True)

    ent = df[df.action == "SELL_PUT"].copy()
    ent["collateral"] = ent.strike * MULT * ent.contracts

    print("--- 1. COLLATERAL per entry -------------------------------------")
    g = ent.groupby("arm")["collateral"]
    print(g.agg(["count", "mean", "median", "sum"]).to_string(
        float_format=lambda v: "%,.0f" % v if abs(v) > 1000 else "%.2f" % v))
    print("\nmean strike:")
    print(ent.groupby("arm")["strike"].agg(["mean", "median"]).to_string())

    print("\n--- 2. DURATION: campaign open -> last trade --------------------")
    life = (df.groupby(["arm", "campaign_id"])["date"]
              .agg(["min", "max"]).reset_index())
    life["days"] = (life["max"] - life["min"]).dt.days
    print(life.groupby("arm")["days"].agg(
        ["count", "mean", "median", "max"]).to_string())

    print("\n--- 3. ASSIGNMENT / wheel continuation --------------------------")
    mix = df.pivot_table(index="arm", columns="action", values="date",
                         aggfunc="count").fillna(0).astype(int)
    print(mix.to_string())
    for arm in ("vol_pctile", "iv_rank"):
        n_ent = int((df[(df.arm == arm) & (df.action == "SELL_PUT")]).shape[0])
        n_asg = int(mix.loc[arm].get("ASSIGNED", 0)) if arm in mix.index else 0
        print("%-12s assignment rate = %d/%d = %.1f%%"
              % (arm, n_asg, n_ent, 100.0 * n_asg / max(n_ent, 1)))

    print("\n--- slot occupancy ----------------------------------------------")
    # Collateral committed as a share of equity: if the sort locks more capital
    # per position, fewer slots can be filled even with identical logic.
    for arm in ("vol_pctile", "iv_rank"):
        a = ent[ent.arm == arm]
        print("%-12s total collateral deployed = %,.0f over %d entries"
              % (arm, a.collateral.sum(), len(a)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
