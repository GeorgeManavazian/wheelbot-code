"""Daily paper-step runner. Data-only, simulated fills, NO order code.

  PYTHONPATH=. .venv-live/bin/python live/run_daily.py --n 5 --capital 100000
  PYTHONPATH=. .venv-live/bin/python live/run_daily.py --smoke   # 3-ticker live test
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import pandas as pd

from src.engine_v2.options.portfolio import PortfolioState, step_one_day
from src.engine_v2.options.wheel import WheelConfig
from live.state import load_state, save_state
from live.market_live import LiveMarket
from live.universe import UNIVERSE
from live.config import load_run_config

STATE_PATH = "data/live/state.json"
TRADES_PATH = "data/live/trades.jsonl"
SNAPSHOTS_PATH = "data/live/snapshots.jsonl"

FROZEN = dict(put_delta=0.30, call_delta=0.50, target_dte=11,
              take_profit_pct=0.60, call_min_strike="basis",
              # rent only names flat on BOTH timescales (2026-07-18):
              #   structural: |50d/200d-1| <= 3%  (no big trend / knife)
              #   tactical:   |9d/20d-1|  <= 1%   (no live leg over our ~11d hold)
              chop_max_ma_spread=0.03, chop_max_fast_spread=0.01)


def _trade_row(t):
    c = t.contract
    return {"date": pd.Timestamp(t.date).isoformat(), "action": t.action,
            "ticker": c.root, "strike": c.strike, "right": c.right,
            "expiry": pd.Timestamp(c.expiry).isoformat(), "contracts": t.contracts,
            "price": t.price_per_contract, "cash_after": t.cash_after,
            "campaign": t.campaign_id}


def paper_step(state, market, cfg, n_slots, trades_path, state_path,
               snapshot_path=None):
    day = market._obs
    result = step_one_day(state, market, day, cfg, selector="chop", n_slots=n_slots)
    # State FIRST (the source of truth). If it saved, the log/snapshot appends
    # that follow are secondary — a failure there leaves state correct + an
    # incomplete log (recoverable), never a log claiming trades the reloaded
    # state doesn't reflect (which could re-trade a position).
    save_state(state, state_path)
    os.makedirs(os.path.dirname(trades_path) or ".", exist_ok=True)
    with open(trades_path, "a") as f:
        for t in result.trades:
            f.write(json.dumps(_trade_row(t)) + "\n")
    if snapshot_path is not None:
        from live.snapshots import snapshot, append_snapshot
        append_snapshot(snapshot_path, snapshot(state, result.equity, day))
    return result


def _live_market(universe, held, obs, client, target_dte, chop_max_ma_spread=None,
                 chop_max_fast_spread=None):
    from live.data import daily_closes, chain_frame
    # pass obs into chain_frame so the chain's `date` column == the run's obs day
    # (else a midnight-crossing run stamps chains with a different date than the
    # engine filters on, silently reading every chain as empty).
    return LiveMarket(universe, held, obs,
                      closes_fn=lambda tk: daily_closes(client, tk),
                      chain_fn=lambda tk: chain_frame(client, tk, target_dte, obs_date=obs),
                      chop_max_ma_spread=chop_max_ma_spread,
                      chop_max_fast_spread=chop_max_fast_spread)


def main():
    ap = argparse.ArgumentParser()
    # default None -> fall through to config.json; a flag still overrides it
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--capital", type=float, default=None)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()

    run_cfg = load_run_config()
    n = args.n if args.n is not None else run_cfg["n"]
    capital = args.capital if args.capital is not None else run_cfg["capital"]

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "schwab"))
    from schwab_client import get_client
    client = get_client()

    universe = ["GDX", "SLV", "XOP"] if args.smoke else UNIVERSE
    obs = pd.Timestamp.today().normalize()
    state = load_state(STATE_PATH) or PortfolioState(cash=capital, positions=[])
    held = {p["ticker"] for p in state.positions}
    cfg = WheelConfig(ticker="SPY", starting_capital=capital, **FROZEN)

    market = _live_market(universe, held, obs, client, cfg.target_dte,
                          cfg.chop_max_ma_spread, cfg.chop_max_fast_spread)
    if market.skipped:
        print(f"skipped {len(market.skipped)} tickers (pull failures): "
              f"{[s[0] for s in market.skipped][:8]}")
    r = paper_step(state, market, cfg, n, TRADES_PATH, STATE_PATH, SNAPSHOTS_PATH)
    print(f"\n=== paper day {obs.date()}  (N={n}, ${capital:,.0f}) ===")
    print(f"trades today: {len(r.trades)}  |  open campaigns: {len(state.positions)}  |  "
          f"cash ${state.cash:,.0f}  |  equity ${r.equity:,.0f}")
    for t in r.trades:
        print(f"  {t.action:<12} {t.contract.root} {t.contract.strike}{t.contract.right} "
              f"x{t.contracts} @ {t.price_per_contract}")


if __name__ == "__main__":
    main()
