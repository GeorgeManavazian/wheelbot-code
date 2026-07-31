"""RTH chain-snapshot runner (A16). Data-only, NO order code, writes nothing
but the snapshot file.

  PYTHONPATH=. .venv-live/bin/python live/run_chain_snapshot.py [--force]

The daily decision runs at 17:00 ET, after the options close, where quotes are
3-4x wider than tradeable. This runner executes DURING regular hours (weekdays
15:20-15:55 ET, fired by wheelbot_tick.sh), pulls the same chains the 17:00
run would have pulled, and persists them via live/chain_store.py; run_daily.py
then consumes the snapshot instead of the live post-close endpoint.

Split, not moved: the decision itself must stay post-close, because expiry
settlement and equity marking need the official 16:00 close (portfolio.py's
settle path refuses anything else). The candidate set (held + good-to-rent) is
computed from PRIOR-session data only (_row_before), so the set pulled here is
identical to the one the 17:00 run computes.

Exit codes: 0 = snapshot saved (tick writes the once-per-day marker);
non-zero = no snapshot (tick retries on every remaining tick in the window).
An EMPTY snapshot (no held, nothing good-to-rent) still saves and exits 0 --
"present but empty" must stay distinguishable from "the window failed".
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")

# Inside RTH (quotes live and tradeable), late enough to be near the state the
# 17:00 decision acts on, ending early enough that a ~7-minute pull started on
# the last allowed tick still finishes by the 16:00 close (skeptic F4: the
# original 15:55 close meant a last-tick start finished ~16:02, past it).
SNAP_OPEN, SNAP_CLOSE = 1520, 1550

# A pull that STARTED in-window may legitimately FINISH a few minutes past
# 16:00 on a slow day -- the book it read is the closing book, not the decayed
# post-close ghost the audit measured at 17:00+. Past this grace, saving would
# stamp genuinely post-close quotes as the blessed RTH snapshot (skeptic F4),
# so the save is refused and the day gaps instead of lying.
SAVE_DEADLINE = 1605


def snapshot_window_open(now_et) -> bool:
    """Weekday 15:20-15:50 ET. 17:00 is NOT in the window -- that is the
    defect (A16), not a fallback."""
    if now_et.weekday() >= 5:
        return False
    hm = now_et.hour * 100 + now_et.minute
    return SNAP_OPEN <= hm <= SNAP_CLOSE


def save_still_rth(now_et) -> bool:
    """Re-checked AFTER the pull, before saving -- the start gate alone lets a
    hung or slow pull bless post-close quotes as RTH."""
    return now_et.hour * 100 + now_et.minute <= SAVE_DEADLINE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="pull outside the RTH window (manual/backfill use only; "
                         "the result is exactly the post-close book A16 bans)")
    args = ap.parse_args()

    from live.universe import UNIVERSE
    from live.accounts import all_accounts, account_paths
    from live.state import load_state
    from live.chain_store import save_chain_snapshot
    from live.config import load_run_config
    from live.run_daily import FROZEN, _live_market, zombie_check
    from src.engine_v2.options.wheel import WheelConfig

    now = dt.datetime.now(ET)
    if not args.force and not snapshot_window_open(now):
        # exit non-zero so the tick never writes the done-marker for a refusal
        print(f"chain snapshot REFUSED: {now:%Y-%m-%d %H:%M} ET is outside the "
              f"{SNAP_OPEN}-{SNAP_CLOSE} RTH window (A16). Use --force only if "
              f"you want the post-close book on purpose.")
        return 1

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "schwab"))
    from schwab_client import get_client
    client = get_client()

    obs = pd.Timestamp(now.date())
    held_all = set()
    for (cap, n) in all_accounts():
        st = load_state(account_paths(cap, n)["state"])
        if st is not None:
            held_all |= {p["ticker"] for p in st.positions}

    cfg = WheelConfig(ticker="SPY", starting_capital=100_000.0, **FROZEN)
    market = _live_market(UNIVERSE, held_all, obs, client, cfg.target_dte,
                          None,  # chains=None: this IS the one legitimate RTH live pull
                          cfg.chop_max_ma_spread, cfg.chop_max_fast_spread,
                          cfg.chop_max_fast_fall)

    thr = load_run_config()["zombie_threshold"]
    if zombie_check(len(market.skipped_closes), len(UNIVERSE),
                    market.chain_attempts, market.chains_ok, thr):
        print(f"chain snapshot FAILED {obs.date()}: price history "
              f"{len(market.skipped_closes)}/{len(UNIVERSE)} failed, chains "
              f"{market.chains_ok}/{market.chain_attempts} usable (threshold "
              f"{thr:.0%}). Nothing saved; the next tick in the window retries.")
        return 1

    done = dt.datetime.now(ET)
    if not args.force and not save_still_rth(done):
        print(f"chain snapshot DISCARDED: pull finished {done:%H:%M} ET, past "
              f"the {SAVE_DEADLINE} grace -- these are post-close quotes and "
              f"must not be blessed as RTH (A16). Nothing saved.")
        return 1

    path = save_chain_snapshot(obs, market._chains, pulled_at=now.isoformat())
    print(f"chain snapshot {obs.date()}: {market.chains_ok}/{market.chain_attempts} "
          f"chains for {len(held_all)} held + good-to-rent -> {path}")
    if market.skipped_chains:
        # Skeptic F3: a sub-threshold chain failure used to freeze that ticker
        # for the whole day (its held leg's TP included) even with ~30 min of
        # window left. Save the partial snapshot -- 17:00 uses the best one
        # written -- but exit non-zero so the tick does NOT write the marker
        # and every remaining in-window tick retries a cleaner pull, which
        # simply overwrites this file.
        print(f"PARTIAL: {len(market.skipped_chains)} chain(s) failed "
              f"({[tk for tk, _ in market.skipped_chains][:8]}); snapshot "
              f"saved anyway; exiting 1 so remaining window ticks retry.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
