"""Daily paper-step runner. Data-only, simulated fills, NO order code.

  PYTHONPATH=. .venv-live/bin/python live/run_daily.py --n 5 --capital 100000
  PYTHONPATH=. .venv-live/bin/python live/run_daily.py --smoke   # 3-ticker live test
"""
from __future__ import annotations

from live.paths import in_state
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
from live.accounts import all_accounts, account_paths, account_label
from live.alerts import send_alert
from live.gaps import append_gap
from live.config import load_run_config

FROZEN = dict(put_delta=0.30, call_delta=0.50, target_dte=11,
              take_profit_pct=0.60, call_min_strike="basis",
              # DOWN-ONLY tactical gate (A/B'd 2026-07-18): reject only a FALLING
              # short-horizon leg (9d/20d < -1%). A put seller loses on a fall, not
              # a rise, so keep up-legs (winners). The symmetric + structural gates
              # backtested worse (symmetric -71% P&L, structural went negative) and
              # were dropped. Guards the AA/VALE falling-knife names.
              chop_max_fast_fall=0.01)


def zombie_check(skipped: int, universe_size: int, threshold: float) -> bool:
    """True when this run pulled so little data that it is a FAILED run, not a
    quiet one. 2026-07-24 is the case this exists for: all 547 tickers failed,
    the run exited 0, booked no trades, and its wrapper wrote the 'done' marker
    anyway -- so a totally dead day was permanently recorded as complete and
    became unrecoverable. A zombie run must leave no marker so the next tick
    retries it."""
    if universe_size <= 0:
        return True
    return (skipped / universe_size) >= threshold


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
                 chop_max_fast_spread=None, chop_max_fast_fall=None):
    from live.data import daily_closes, chain_frame
    # pass obs into chain_frame so the chain's `date` column == the run's obs day
    # (else a midnight-crossing run stamps chains with a different date than the
    # engine filters on, silently reading every chain as empty).
    return LiveMarket(universe, held, obs,
                      closes_fn=lambda tk: daily_closes(client, tk),
                      chain_fn=lambda tk: chain_frame(client, tk, target_dte, obs_date=obs),
                      chop_max_ma_spread=chop_max_ma_spread,
                      chop_max_fast_spread=chop_max_fast_spread,
                      chop_max_fast_fall=chop_max_fast_fall)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",  # 1 account, 3 tickers, throwaway store
                    help="quick live connectivity check (100k/N5 on GDX/SLV/XOP -> _smoke store)")
    args = ap.parse_args()

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "schwab"))
    from schwab_client import get_client
    client = get_client()

    universe = ["GDX", "SLV", "XOP"] if args.smoke else UNIVERSE
    obs = pd.Timestamp.today().normalize()
    # strategy params are shared across accounts; capital/N vary per account
    cfg = WheelConfig(ticker="SPY", starting_capital=100_000.0, **FROZEN)
    accounts = [(100_000, 5)] if args.smoke else all_accounts()

    # Load every account's state FIRST so one shared market pull covers the union
    # of all held tickers (chains are pulled once for held-all + good-to-rent).
    loaded = {}
    held_all = set()
    for (cap, n) in accounts:
        paths = _paths(cap, n, smoke=args.smoke)
        state = load_state(paths["state"]) or PortfolioState(cash=float(cap), positions=[])
        loaded[(cap, n)] = (state, paths)
        held_all |= {p["ticker"] for p in state.positions}

    market = _live_market(universe, held_all, obs, client, cfg.target_dte,
                          cfg.chop_max_ma_spread, cfg.chop_max_fast_spread,
                          cfg.chop_max_fast_fall)
    if market.skipped:
        print(f"skipped {len(market.skipped)} tickers (pull failures): "
              f"{[s[0] for s in market.skipped][:8]}")

    run_cfg = load_run_config()
    if zombie_check(len(market.skipped), len(universe), run_cfg["zombie_threshold"]):
        day = str(obs.date())
        msg = (f"{day}: pull failed for {len(market.skipped)}/{len(universe)} "
               f"tickers (threshold {run_cfg['zombie_threshold']:.0%}). No state "
               f"was touched and no completion marker was written -- the next "
               f"tick will retry. Likely a lapsed Schwab token or an outage.")
        print(f"ZOMBIE RUN -- {msg}")
        send_alert(f"daily run FAILED {day}", msg)
        append_gap(day, "pull_failure",
                   skipped=len(market.skipped), universe=len(universe))
        return 1

    print(f"\n=== paper day {obs.date()} — {len(accounts)} account(s), down-only gate ===")
    print(f"{'account':<10}{'trades':>7}{'open':>6}{'cash':>13}{'equity':>13}")
    for (cap, n) in accounts:
        state, paths = loaded[(cap, n)]
        label = "_smoke" if args.smoke else account_label(cap, n)
        # isolate each account: one account's failure (bad chain, edge case) must not
        # abort the other 24 for the day (silent multi-account gap in an unattended run).
        try:
            os.makedirs(paths["dir"], exist_ok=True)
            acfg = WheelConfig(ticker="SPY", starting_capital=float(cap), **FROZEN)
            r = paper_step(state, market, acfg, n, paths["trades"], paths["state"],
                           paths["snapshots"])
            print(f"{label:<10}{len(r.trades):>7}{len(state.positions):>6}"
                  f"{state.cash:>13,.0f}{r.equity:>13,.0f}")
        except Exception as e:
            print(f"{label:<10} ERROR: {type(e).__name__}: {e} — skipped, others continue")
    return 0


def _paths(capital, n, smoke=False):
    if smoke:
        d = in_state("accounts", "_smoke")
        return {"dir": d, "state": os.path.join(d, "state.json"),
                "trades": os.path.join(d, "trades.jsonl"),
                "snapshots": os.path.join(d, "snapshots.jsonl")}
    return account_paths(capital, n)


if __name__ == "__main__":
    sys.exit(main())
