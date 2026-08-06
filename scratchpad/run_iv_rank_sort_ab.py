"""IV rank as the SORT KEY -- A/B on the real engine.

The veto A/B (run_iv_rank_ab.py, 2026-08-04) rejected min_iv_rank: it raises
P&L per campaign ~14% and HALVES campaign count 254 -> 130, costing 18 points
of total return. The signal is real; the instrument was wrong, because the
wheel's return comes from capital turnover and a refused entry leaves the slot
idle.

This runs the same signal as a SORT. One axis, one knob: `rank_by`. Every
other config value is identical to the veto A/B, and the baseline arm is
byte-identical to that run's baseline arm, so the two tables stack directly.

    baseline    rank_by="vol_pctile"   (realized vol, highest first -- today)
    iv sort     rank_by="iv_rank"      (relative IV, highest first)

BASELINE TO REPRODUCE (veto A/B, 2026-08-04):
    P&L 42,920 | total +43.0% | Sharpe 1.10 | maxDD -15.5% | 254 campaigns

THE TRIPWIRE: campaign count must stay near 254. A sort reorders the pool; it
never removes a member. If campaigns fall, something is acting as a filter and
this run is not measuring what it claims to -- do not read the return column
until that is explained.

Judge on Sharpe and return-per-drawdown, not total return alone. Delta behaves
as a leverage dial in this engine and the original sweep sorted by total
return, which guarantees the most leveraged arm wins.

Universe: fixtures/iv_rank_ab_universe.json -- the names the veto A/B ran on,
persisted 2026-08-05 because the original list lived only in a session temp
dir (the same way the sweep's 152-ticker universe was lost). NOTE: this is NOT
the sweep's universe, so absolute levels here do not reproduce the +47.6%
table. The comparison BETWEEN arms is the result.

Run: PYTHONPATH=. .venv/bin/python scratchpad/run_iv_rank_sort_ab.py
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

# Identical to run_iv_rank_ab.py's BASE. Do not "improve" it here -- the whole
# value of this run is that it is comparable to the veto table.
BASE = dict(put_delta=0.40, call_delta=0.50, target_dte=7,
            take_profit_pct=0.25, starting_capital=100_000.0,
            call_min_strike="basis",
            min_ann_yield_on_collateral=0.08,
            max_credit_pct_of_strike=0.03)

ARMS = [("baseline (vol_pctile)", "vol_pctile"),
        ("iv_rank sort", "iv_rank")]

N_SLOTS = 5

# The veto A/B's baseline row, for an at-a-glance drift check.
EXPECTED_BASELINE = dict(total=0.430, sharpe=1.10, campaigns=254)


def perf(eq):
    ppy = m.infer_periods_per_year(eq.index)
    rets = eq.pct_change().fillna(0.0)
    return (float(eq.iloc[-1] / eq.iloc[0] - 1), m.cagr(eq, ppy),
            m.sharpe(rets, ppy), m.max_drawdown(eq))


def main():
    universe = json.loads(UNIVERSE_FILE.read_text())
    print("universe: %d tickers (from %s)" % (len(universe), UNIVERSE_FILE),
          flush=True)

    cfg0 = WheelConfig(ticker="SPY", **BASE)

    # Rank against each ticker's FULL stored history, the way step 1 measured
    # it -- not against the backtest window. Windowing first would leave every
    # name unrankable for the first ~150 trading days and then rank against a
    # truncated history, a different statistic than the one measured.
    chains, states, series = {}, {}, {}
    for i, t in enumerate(universe, 1):
        try:
            full = pd.read_parquet(chain_path(t))
        except Exception as e:
            print("  no chain for %s (%s)" % (t, e), flush=True)
            continue
        full["date"] = pd.to_datetime(full["date"])
        ch = full[(full["date"] >= START) & (full["date"] <= END)]
        if ch.empty:
            continue
        try:
            st = regime_series(closes_for(t))
        except Exception as e:
            print("  no regime state for %s (%s)" % (t, e), flush=True)
            continue
        one = IVHistory.from_chains({t: full}, cfg0)
        if one.known(t):
            series[t] = one._by[t]
        chains[t], states[t] = ch, st
        del full
        if i % 25 == 0:
            print("  loaded %d/%d" % (i, len(universe)), flush=True)

    print("loaded %d tickers" % len(chains), flush=True)
    hist = IVHistory(series)
    med = int(pd.Series({k: len(v) for k, v in series.items()}).median())
    print("ranked series for %d tickers, built on full history "
          "(median %d observations)" % (len(hist), med), flush=True)

    # How many loaded names have NO usable rank at all. This is the size of the
    # population the neutral-0.5 decision governs: if it is ~0 the decision was
    # cosmetic, if it is large it drives the result and the arm needs reading
    # with that in mind.
    unrankable = sorted(set(chains) - set(series))
    print("unrankable at load: %d of %d -- %s\n"
          % (len(unrankable), len(chains),
             ", ".join(unrankable) if unrankable else "none"), flush=True)

    hdr = ("%-24s %10s %8s %7s %7s %7s %11s %9s"
           % ("arm", "P&L", "total", "CAGR", "Sharpe", "maxDD", "campaigns",
              "neutral"))
    print(hdr)
    print("-" * len(hdr))

    rows = {}
    for name, rank_by in ARMS:
        cfg = WheelConfig(ticker="SPY", **BASE, rank_by=rank_by)
        res = run_portfolio_wheel(
            chains, cfg, states, selector="chop", n_slots=N_SLOTS,
            universe=sorted(chains),
            iv_history=hist if rank_by == "iv_rank" else None)
        tot, cagr, sh, dd = perf(res.equity)
        # Distinct (day, ticker) pairs that were ranked on a neutral because
        # their rank could not be measured -- deduped in the engine already.
        neutral = sum(1 for w in res.warnings
                      if w[1] == "entry_ranked_iv_unknown")
        rows[rank_by] = dict(total=tot, sharpe=sh,
                             campaigns=res.n_campaigns_opened)
        print("{:<24} {:>10,.0f} {:>7.1f}% {:>6.1f}% {:>7.2f} {:>6.1f}% "
              "{:>11d} {:>9d}".format(
                  name, res.equity.iloc[-1] - 100_000, tot * 100, cagr * 100,
                  sh, dd * 100, res.n_campaigns_opened, neutral), flush=True)

    bh = buy_hold_curve(chains["SPY"], 100_000.0) if "SPY" in chains else None
    if bh is not None:
        tot, cagr, sh, dd = perf(bh)
        print("{:<24} {:>10,.0f} {:>7.1f}% {:>6.1f}% {:>7.2f} {:>6.1f}%".format(
            "buy-hold SPY", bh.iloc[-1] - 100_000, tot * 100, cagr * 100,
            sh, dd * 100))

    # --- the two checks that decide whether the table above is readable -----
    print()
    b, s = rows["vol_pctile"], rows["iv_rank"]

    drift = abs(b["total"] - EXPECTED_BASELINE["total"])
    if drift > 0.005 or b["campaigns"] != EXPECTED_BASELINE["campaigns"]:
        print("!! BASELINE DRIFT: got %+.1f%% / %d campaigns, veto A/B recorded "
              "%+.1f%% / %d. The arms are no longer comparable to that table -- "
              "explain this before reading the sort result."
              % (b["total"] * 100, b["campaigns"],
                 EXPECTED_BASELINE["total"] * 100,
                 EXPECTED_BASELINE["campaigns"]), flush=True)
    else:
        print("baseline reproduces the veto A/B: %+.1f%% / %d campaigns."
              % (b["total"] * 100, b["campaigns"]), flush=True)

    lost = b["campaigns"] - s["campaigns"]
    if abs(lost) > 0.05 * b["campaigns"]:
        print("!! TRIPWIRE: campaign count moved %+d (%d -> %d). A sort must "
              "not change HOW MANY entries happen -- something is filtering. "
              "Do not read the return column until this is explained."
              % (-lost, b["campaigns"], s["campaigns"]), flush=True)
    else:
        print("tripwire OK: campaigns %d -> %d (%+d). The sort reordered the "
              "pool without shrinking it."
              % (b["campaigns"], s["campaigns"], -lost), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
