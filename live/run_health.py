"""Health-check entrypoint, fired by the 5-minute tick. Sub-second no-op on
weekends, before the cutoff, or on a day that already completed.

  PYTHONPATH=. .venv-live/bin/python live/run_health.py
"""
from __future__ import annotations
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

from live.health import check_day, unalerted_gaps, EOD_WINDOW_CLOSE
from live.alerts import send_alert

ET = ZoneInfo("America/New_York")


def run_check(now_et, logs_dir=None, gaps_path=None, send=send_alert) -> str:
    """One health pass: scan + record missed days (check_day), then alert
    every recorded-but-undelivered day, naming the ACTUAL dates (D12) and
    suppressing on a .gapalerted delivered-marker per day (D3) -- a failed
    send retries on the next tick instead of losing the alarm forever."""
    from live.health import LOGS_DIR
    from live.gaps import GAPS_PATH
    ldir = logs_dir or LOGS_DIR
    gpath = gaps_path or GAPS_PATH
    status, _missed = check_day(now_et, ldir, gpath)
    if status in ("not_weekday", "too_early"):
        return status
    days = unalerted_gaps(gpath, ldir, now_et.date())
    if not days:
        print(f"health {now_et:%Y-%m-%d %H:%M}: {status}")
        return status
    names = ", ".join(days)
    msg = (f"No successful daily run for: {names} -- the retry window closed "
           f"at {EOD_WINDOW_CLOSE} ET with no completion marker.\n\n"
           f"Those days' entries, assignments and expiries did not happen "
           f"and CANNOT be backfilled: Schwab has no historical "
           f"option-chain endpoint. Logged to data/live/gaps.jsonl so "
           f"every future report discloses them.\n\n"
           f"Check: is the VPS up? Has the Schwab token lapsed?")
    print(f"GAP -- {msg}")
    if send(f"MISSED TRADING DAY(S) {names}", msg):
        for day in days:
            open(os.path.join(ldir, f".gapalerted-{day}"), "w").close()
    return status


def main() -> int:
    run_check(dt.datetime.now(ET).replace(tzinfo=None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
