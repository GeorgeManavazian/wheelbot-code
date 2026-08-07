"""Universe-wide IV accrual runner. Data-only, NO order code; writes nothing but
the daily IV observation file.

  PYTHONPATH=. .venv-live/bin/python live/run_iv_accrual.py [--force]

WHY THIS EXISTS. market_live.py pulls option chains only for held tickers plus
that day's weather-gate passers -- 12 of 547 on 2026-07-31. An IV observation
can only exist on a day the chain was pulled, so a ticker accrues ~5-6 per year
against IVHistory's MIN_RANK_OBS = 150. Purchased history would roll out of the
252-day window faster than it accrues, and about a year after launch every name
would fall to NEUTRAL_IV_RANK and selection would silently become universe
order. This runner is what makes "forward data comes from Schwab" true.

SEPARATE from run_chain_snapshot.py on purpose. That runner's exit code gates
the tick's once-per-day marker and its chain_attempts feeds zombie_check; adding
45x the API work to it would put every trading decision behind a heavier pull
and force those thresholds to be re-derived. This one is gated on the trading
snapshot ALREADY existing, so trading gets the window and the API budget first,
and its own exit code touches nothing but its own marker.

Exit codes: 0 = observations saved (tick writes the marker); non-zero = nothing
saved / partial, tick retries on every remaining in-window tick.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

import pandas as pd

from live.iv_accrual import ACCRUAL_GRID, observations_for

ET = ZoneInfo("America/New_York")

# Opens with the trading snapshot's window but is gated on that snapshot having
# saved, so in practice this starts ~15:27-15:35. Closes later than the trading
# window (1550) because a 547-ticker pull takes ~5 minutes.
IV_OPEN, IV_CLOSE = 1520, 1555
# Same grace and same reason as run_chain_snapshot.SAVE_DEADLINE: a pull that
# STARTED in-window may finish a few minutes past 16:00 on a slow day, but past
# this the quotes are genuinely post-close and must not be stamped as RTH.
SAVE_DEADLINE = 1605


def accrual_window_open(now_et) -> bool:
    if now_et.weekday() >= 5:
        return False
    hm = now_et.hour * 100 + now_et.minute
    return IV_OPEN <= hm <= IV_CLOSE


def save_still_rth(now_et) -> bool:
    """Re-checked AFTER the pull -- the start gate alone lets a slow pull bless
    post-close quotes."""
    return now_et.hour * 100 + now_et.minute <= SAVE_DEADLINE


def frozen_cell_on_grid(frozen: dict, grid=ACCRUAL_GRID) -> bool:
    """Is the live config one of the cells we record?

    Without this check, changing FROZEN to an off-grid (put_delta, target_dte)
    leaves the store filling happily with six series NONE of which is the
    strategy being traded -- healthy-looking, and useless."""
    return (float(frozen["put_delta"]), int(frozen["target_dte"])) in \
        [(float(d), int(t)) for d, t in grid]


def pull_failed(attempted: int, ok: int, threshold: float) -> bool:
    """Judge the FEED, never the derived quantity -- the zombie_check doctrine.

    A low observation count is a real property of thin expiry ladders (AOS was
    measured with an in-band expiry on 17% of days), so it is logged and never
    a failure. A high CHAIN failure ratio is an outage."""
    if attempted <= 0:
        return True
    return (attempted - ok) / attempted >= threshold


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="pull outside the RTH window (manual/backfill only; "
                         "the result is the post-close book, not RTH)")
    args = ap.parse_args()

    from live.chain_store import load_chain_snapshot
    from live.config import load_run_config
    from live.data import otm_put_frame
    from live.iv_store import load_iv_day, save_iv_day, tickers_done
    from live.run_daily import FROZEN
    from live.universe import UNIVERSE

    now = dt.datetime.now(ET)
    if not args.force and not accrual_window_open(now):
        print(f"IV accrual REFUSED: {now:%Y-%m-%d %H:%M} ET is outside the "
              f"{IV_OPEN}-{IV_CLOSE} RTH window.")
        return 1

    if not frozen_cell_on_grid(FROZEN):
        print(f"IV accrual REFUSED: FROZEN is put_delta="
              f"{FROZEN['put_delta']} target_dte={FROZEN['target_dte']}, which "
              f"is NOT in ACCRUAL_GRID {ACCRUAL_GRID}. The store would accrue "
              f"six series none of which is the traded strategy. Add the cell "
              f"to the grid (it accrues forward only) or revert FROZEN.")
        return 1

    obs = pd.Timestamp(now.date())

    # Trading eats first: this runner exists to accrue data, never to compete
    # with the decision path for the window or the API budget.
    if load_chain_snapshot(obs) is None:
        print(f"IV accrual DEFERRED: no trading chain snapshot for "
              f"{obs.date()} yet. Retrying on a later in-window tick.")
        return 1

    prior = load_iv_day(obs) or []
    done = tickers_done(prior)
    todo = [tk for tk in UNIVERSE if tk not in done]
    if done:
        print(f"IV accrual resuming: {len(done)} ticker(s) already recorded "
              f"today, {len(todo)} to go.")

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                    "scripts", "schwab"))
    from schwab_client import get_client
    client = get_client()

    records, attempted, ok, skipped = list(prior), 0, 0, []
    for tk in todo:
        attempted += 1
        try:
            frame = otm_put_frame(client, tk, obs_date=obs)
        except Exception as e:      # one bad symbol must not stop the run
            skipped.append((tk, f"{type(e).__name__}: {e}"))
            continue
        ok += 1
        records.extend(observations_for(tk, frame, obs))

    thr = load_run_config()["zombie_threshold"]
    if pull_failed(attempted, ok, thr):
        print(f"IV accrual FAILED {obs.date()}: chains {ok}/{attempted} usable "
              f"(threshold {thr:.0%}). Nothing saved; the next in-window tick "
              f"resumes.")
        return 1

    finished = dt.datetime.now(ET)
    if not args.force and not save_still_rth(finished):
        print(f"IV accrual DISCARDED: pull finished {finished:%H:%M} ET, past "
              f"the {SAVE_DEADLINE} grace -- these are post-close quotes and "
              f"must not be stamped RTH. Nothing saved.")
        return 1

    path = save_iv_day(obs, records, pulled_at=now.isoformat())
    cells = len(records)
    names = len({r["ticker"] for r in records})
    print(f"IV accrual {obs.date()}: {ok}/{attempted} chains, {cells} "
          f"observation(s) across {names} ticker(s) -> {path}")
    if skipped:
        print(f"PARTIAL: {len(skipped)} chain(s) failed "
              f"({[tk for tk, _ in skipped][:8]}); saved anyway; exiting 1 so "
              f"remaining in-window ticks resume the missing names.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
