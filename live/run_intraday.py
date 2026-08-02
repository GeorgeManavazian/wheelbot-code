"""Intraday exit-only runner. Fired every ~15 min; a hard market-hours gate makes
it a sub-second no-op outside 9:30-4 ET on weekdays (no Schwab call). In-hours it
pulls live marks for each account's HELD legs and closes any that hit take-profit
at the live ask. Entries stay in the 5pm EOD run (run_daily.py).

  PYTHONPATH=. .venv-live/bin/python live/run_intraday.py
"""
from __future__ import annotations
import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

import pandas as pd

from src.engine_v2.options.wheel import WheelConfig
from live.config import load_run_config
from live.state import load_state, save_state
from live.accounts import all_accounts, account_paths, account_label
from live.intraday import manage_intraday
from live.run_daily import FROZEN, _trade_row

ET = ZoneInfo("America/New_York")
OPEN, CLOSE = dt.time(9, 30), dt.time(16, 0)


def market_is_open(now_et) -> bool:
    """Weekday 9:30-16:00 ET. (Holidays aren't special-cased: on a holiday the
    market is shut, live quotes are stale, so no TP fires -- a harmless no-op.)"""
    return now_et.weekday() < 5 and OPEN <= now_et.timetz().replace(tzinfo=None) <= CLOSE


def main() -> int:
    """D1: the EXIT CODE is the dead-man's contract. 0 = clean pass (or an
    honest out-of-hours no-op); nonzero = any account handler raised, or the
    client/quote layer failed wholesale. The old runner returned None (exit 0)
    even with all 25 accounts erroring, and the tick's case-sensitive
    `grep ERROR` was the only tripwire -- a bare traceback slipped it."""
    # A8: refuse real_money BEFORE the market gate -- a misconfigured box must
    # scream on the very next 5-min tick, not at the next open. No reconciler
    # or order code exists; exit 3 + the ERROR literal trips the tick's grep.
    if load_run_config()["real_money"]:
        print("ERROR A8: real_money=true but no position reconciler is wired "
              "-- refusing. Set real_money back to false.")
        return 3

    now_et = dt.datetime.now(ET)
    if not market_is_open(now_et):
        print(f"market closed ({now_et:%Y-%m-%d %H:%M %Z}) — intraday skip")
        return 0

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "schwab"))
    try:
        from schwab_client import get_client
        from live.marks import contract_quotes
        client = get_client()
    except Exception as e:                     # noqa: BLE001 -- deliberate
        # wholesale failure (token lapse, import rot): no account can run
        print(f"ERROR wholesale: {type(e).__name__}: {e} — no account ran")
        return 2
    cfg = WheelConfig(ticker="SPY", starting_capital=100_000.0, **FROZEN)
    now = pd.Timestamp(now_et.replace(tzinfo=None))

    total, errors = 0, 0
    for cap, n in all_accounts():
        try:
            paths = account_paths(cap, n)
            state = load_state(paths["state"])
            if state is None or not state.positions:
                continue
            quotes = contract_quotes(client, state.positions)
            if not quotes:
                continue
            trades = manage_intraday(state, quotes, cfg, now)
            if not trades:
                continue
            save_state(state, paths["state"])          # state FIRST (source of truth)
            with open(paths["trades"], "a") as f:
                for t in trades:
                    f.write(json.dumps(_trade_row(t)) + "\n")
            total += len(trades)
            print(f"{account_label(cap, n)}: closed {len(trades)} at TP "
                  f"({', '.join(t.contract.root for t in trades)})")
        except Exception as e:
            errors += 1
            print(f"{account_label(cap, n)}: ERROR {type(e).__name__}: {e} — skipped")
    print(f"intraday {now_et:%H:%M %Z} — {total} TP close(s) across 25 accounts")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
