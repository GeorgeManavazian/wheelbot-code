"""IV-rank gate A/B on the real engine.

One axis, one knob. Both arms share every other config value, so the only
difference between them is `min_iv_rank`. Arms:

    baseline      min_iv_rank = None    (the sweep's winning config)
    gate 0.88     min_iv_rank = 0.88    (measured zero-crossing)
    gate 0.90     min_iv_rank = 0.90    (measured +13.7 bps)
    gate 0.95     min_iv_rank = 0.95    (measured +19.7 bps, thinner)

Universe: the 153 tickers that survived the step-1 measurement, read from its
output rather than re-derived, so the A/B is run on exactly the names the
evidence came from. NOTE: this is NOT the sweep's 152-ticker universe -- that
one was never persisted (see the 2026-08-04 live-paper log) -- so absolute
returns here will not reproduce the +47.6% table. The comparison BETWEEN arms
is the result; the level is not comparable to the sweep.

Run: PYTHONPATH=. .venv/bin/python scratchpad/run_iv_rank_ab.py
"""
from __future__ import annotations

import sys

import pandas as pd

from src.engine_v2.backtest import metrics_simple as m
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.iv_rank import IVHistory
from src.engine_v2.options.portfolio import run_portfolio_wheel
from src.engine_v2.options.report import buy_hold_curve
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.regime.data import closes_for
from src.engine_v2.regime.state import regime_series

TRADES = ("/private/tmp/claude-501/-Users-georgiemanavazian-Documents-Trading-vault/"
          "1b0bf6a9-903a-4045-9aef-3169be0d6c98/scratchpad/trades_full.parquet")

START = pd.Timestamp("2024-01-15")
END = pd.Timestamp("2026-07-01")

# The sweep's winning arm, plus the live yield floor.
BASE = dict(put_delta=0.40, call_delta=0.50, target_dte=7,
            take_profit_pct=0.25, starting_capital=100_000.0,
            call_min_strike="basis",
            min_ann_yield_on_collateral=0.08,
            max_credit_pct_of_strike=0.03)

ARMS = [("baseline (gate off)", None),
        ("iv_rank >= 0.88", 0.88),
        ("iv_rank >= 0.90", 0.90),
        ("iv_rank >= 0.95", 0.95)]

N_SLOTS = 5


def perf(eq):
    ppy = m.infer_periods_per_year(eq.index)
    rets = eq.pct_change().fillna(0.0)
    return (float(eq.iloc[-1] / eq.iloc[0] - 1), m.cagr(eq, ppy),
            m.sharpe(rets, ppy), m.max_drawdown(eq))


def main():
    universe = sorted(pd.read_parquet(TRADES)["ticker"].unique())
    print("universe: %d tickers" % len(universe), flush=True)

    cfg0 = WheelConfig(ticker="SPY", **BASE)

    # The rank must be computed against the ticker's FULL stored history, the
    # way step 1 measured it -- not against the backtest window. Windowing
    # first would leave the gate structurally inert for the first ~150 trading
    # days (min_obs) and then rank against a truncated history, which is a
    # different statistic than the one the 0.90 floor was measured on.
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
    print("ranked series for %d tickers, built on full history "
          "(median %d observations)\n"
          % (len(hist), int(pd.Series({k: len(v) for k, v in series.items()}).median())),
          flush=True)

    hdr = ("%-22s %10s %8s %7s %7s %7s %10s %8s"
           % ("arm", "P&L", "total", "CAGR", "Sharpe", "maxDD", "campaigns", "gated"))
    print(hdr)
    print("-" * len(hdr))

    for name, floor in ARMS:
        cfg = WheelConfig(ticker="SPY", **BASE, min_iv_rank=floor)
        res = run_portfolio_wheel(chains, cfg, states, selector="chop",
                                  n_slots=N_SLOTS, universe=sorted(chains),
                                  iv_history=hist if floor is not None else None)
        tot, cagr, sh, dd = perf(res.equity)
        gated = sum(1 for w in res.warnings if w[1] == "entry_gated_iv_rank")
        print("{:<22} {:>10,.0f} {:>7.1f}% {:>6.1f}% {:>7.2f} {:>6.1f}% "
              "{:>10d} {:>8d}".format(name, res.equity.iloc[-1] - 100_000,
                                      tot * 100, cagr * 100, sh, dd * 100,
                                      res.n_campaigns_opened, gated), flush=True)

    bh = buy_hold_curve(chains["SPY"], 100_000.0) if "SPY" in chains else None
    if bh is not None:
        tot, cagr, sh, dd = perf(bh)
        print("{:<22} {:>10,.0f} {:>7.1f}% {:>6.1f}% {:>7.2f} {:>6.1f}%".format(
            "buy-hold SPY", bh.iloc[-1] - 100_000, tot * 100, cagr * 100,
            sh, dd * 100))
    return 0


if __name__ == "__main__":
    sys.exit(main())
