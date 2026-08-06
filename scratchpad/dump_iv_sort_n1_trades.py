"""Full trade log for the n_slots=1 / rank_by="iv_rank" arm (+95.4%).

This is the arm from diag_iv_sort_slot_starvation.py: 76 campaigns, +95.4%,
Sharpe 1.20, maxDD -17.1%. Its trades were never persisted -- the fill-realism
dump saved the BASELINE arm only -- so this re-runs it and writes every trade.

Caveats that travel with these rows (see the vault note): n_slots=1 is a
diagnostic axis nobody has validated, the universe is 154 names against a live
531, the config is the sweep's arm (delta 0.40 / DTE 7 / TP 25%) not the frozen
live one, the down-only chop gate is OFF, fees are $0, and no liquidity gate ran.

Run: PYTHONPATH=. .venv/bin/python scratchpad/dump_iv_sort_n1_trades.py
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
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.regime.data import closes_for
from src.engine_v2.regime.state import regime_series

UNIVERSE_FILE = Path("fixtures/iv_rank_ab_universe.json")
OUT_CSV = Path("scratchpad/iv_sort_n1_trades.csv")

START = pd.Timestamp("2024-01-15")
END = pd.Timestamp("2026-07-01")
BASE = dict(put_delta=0.40, call_delta=0.50, target_dte=7,
            take_profit_pct=0.25, starting_capital=100_000.0,
            call_min_strike="basis",
            min_ann_yield_on_collateral=0.08,
            max_credit_pct_of_strike=0.03)


def main():
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
    hist = IVHistory(series)
    print("loaded %d tickers" % len(chains), flush=True)

    cfg = WheelConfig(ticker="SPY", **BASE, rank_by="iv_rank")
    res = run_portfolio_wheel(chains, cfg, states, selector="chop", n_slots=1,
                              universe=sorted(chains), iv_history=hist)
    eq = res.equity
    ppy = m.infer_periods_per_year(eq.index)
    tot = float(eq.iloc[-1] / eq.iloc[0] - 1)
    print("campaigns={}  trades={}  final equity={:,.0f}  total={:+.1f}%  "
          "Sharpe={:.2f}  maxDD={:.1f}%".format(
              res.n_campaigns_opened, len(res.trades), eq.iloc[-1], tot * 100,
              m.sharpe(eq.pct_change().fillna(0.0), ppy),
              m.max_drawdown(eq) * 100), flush=True)

    rows = []
    for t in res.trades:
        rows.append(dict(
            date=pd.Timestamp(t.date).date(),
            action=t.action,
            ticker=t.contract.root,
            right=t.contract.right,
            strike=float(t.contract.strike),
            expiry=pd.Timestamp(t.contract.expiry).date(),
            contracts=int(t.contracts),
            price=round(float(t.price_per_contract), 4),
            cash_after=round(float(t.cash_after), 2),
            campaign=int(t.campaign_id),
        ))
    df = pd.DataFrame(rows).sort_values(["date", "campaign", "action"])
    df.to_csv(OUT_CSV, index=False)
    print("wrote %s (%d rows)" % (OUT_CSV, len(df)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
