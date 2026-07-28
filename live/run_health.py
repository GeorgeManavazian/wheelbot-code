"""Health-check entrypoint, fired by the 5-minute tick. Sub-second no-op on
weekends, before the cutoff, or on a day that already completed.

  PYTHONPATH=. .venv-live/bin/python live/run_health.py
"""
from __future__ import annotations
import datetime as dt
import sys
from zoneinfo import ZoneInfo

from live.health import check_day
from live.alerts import send_alert

ET = ZoneInfo("America/New_York")


def main() -> int:
    now = dt.datetime.now(ET)
    result = check_day(now.replace(tzinfo=None))
    if result == "gap_recorded":
        day = now.strftime("%Y-%m-%d")
        msg = (f"No successful daily run for {day} -- the retry window closed "
               f"at 20:00 ET with no completion marker.\n\n"
               f"That day's entries, assignments and expiries did not happen "
               f"and CANNOT be backfilled: Schwab has no historical "
               f"option-chain endpoint. Logged to data/live/gaps.jsonl so "
               f"every future report discloses it.\n\n"
               f"Check: is the VPS up? Has the Schwab token lapsed?")
        print(f"GAP -- {msg}")
        send_alert(f"MISSED TRADING DAY {day}", msg)
    else:
        print(f"health {now:%Y-%m-%d %H:%M %Z}: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
