"""Health-check entrypoint, fired by the 5-minute tick. Sub-second no-op on
weekends, before the cutoff, or on a day that already completed.

  PYTHONPATH=. .venv-live/bin/python live/run_health.py
"""
from __future__ import annotations
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

from live.health import (check_day, unalerted_gaps, intraday_last_tick,
                         EOD_WINDOW_CLOSE, INTRADAY_ALIVE_HHMM)
from live.alerts import send_alert

ET = ZoneInfo("America/New_York")


def run_check(now_et, logs_dir=None, gaps_path=None, send=send_alert):
    """One health pass: scan + record missed days (check_day), then alert
    every recorded-but-undelivered day, naming the ACTUAL dates (D12) and
    suppressing on a .gapalerted delivered-marker per day (D3) -- a failed
    send retries on the next tick instead of losing the alarm forever.

    Returns (status, undelivered): undelivered counts alert attempts that
    FAILED delivery this pass, so main() can exit nonzero and the tick's
    FAIL/OnFailure machinery sees a gap-found-but-unalerted night (group
    skeptic F3 -- it used to be an exit-0 night)."""
    from live.health import LOGS_DIR
    from live.gaps import GAPS_PATH
    ldir = logs_dir or LOGS_DIR
    gpath = gaps_path or GAPS_PATH
    status, _missed = check_day(now_et, ldir, gpath)
    undelivered = 0
    if status in ("not_weekday", "too_early"):
        return status, undelivered
    # D1: nightly post-hoc intraday liveness. Only on a day whose EOD
    # completed (VPS provably alive in the evening) -- a fully-dead day is
    # the missed-day alert's job, not a duplicate email.
    today = now_et.strftime("%Y-%m-%d")
    imarker = os.path.join(ldir, f".intradayalerted-{today}")
    if (os.path.exists(os.path.join(ldir, f".dailyran-{today}"))
            and not os.path.exists(imarker)):
        last = intraday_last_tick(ldir, today)
        if last is None or last < INTRADAY_ALIVE_HHMM:
            what = (f"last tick stamp {last} ET" if last
                    else "NO intraday ticks at all")
            body = (f"The intraday exit manager did not run to the close on "
                    f"{today}: {what} (expected past {INTRADAY_ALIVE_HHMM} "
                    f"ET). Take-profit exits after that point never fired -- "
                    f"a dead exit engine looks identical to a quiet day.\n\n"
                    f"Check intraday-{today}.log on the VPS.")
            print(f"INTRADAY DEAD -- {body}")
            if send(f"intraday manager died mid-session {today}", body):
                open(imarker, "w").close()
            else:
                undelivered += 1

    days = unalerted_gaps(gpath, ldir, now_et.date())
    if not days:
        print(f"health {now_et:%Y-%m-%d %H:%M}: {status}")
        return status, undelivered
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
    else:
        undelivered += 1
    return status, undelivered


def main() -> int:
    _status, undelivered = run_check(dt.datetime.now(ET).replace(tzinfo=None))
    return 1 if undelivered else 0


if __name__ == "__main__":
    sys.exit(main())
