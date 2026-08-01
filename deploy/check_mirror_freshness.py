"""D14: mirror-side freshness check, run by GitHub Actions INSIDE the synced
state repo's checkout (owner decision 2026-08-01: the GitHub robot is the
only off-VPS watcher). Standalone on purpose -- no repo imports, stdlib only.

Judgment: on a weekday evening (the cron fires ~20:30-21:30 ET), the mirror's
heartbeat.json must be from TODAY (ET) and show the EOD run completed. A dead
VPS, a wedged sync, or a broken tick all surface here as a stale or
incomplete heartbeat. Weekends pass unconditionally. Real NYSE holidays pass
because the bot writes its completion marker on holiday-classified days too
(run_daily exits 0), so dailyran is true.

Exit 0 = fresh; nonzero = stale/broken, with the reason on stdout (the
workflow files it into an issue)."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def check(root: str, now_utc_iso: str = None) -> int:
    if now_utc_iso:
        now_utc = dt.datetime.fromisoformat(now_utc_iso.replace("Z", "+00:00"))
    else:
        now_utc = dt.datetime.now(dt.timezone.utc)
    now_et = now_utc.astimezone(ET)
    if now_et.weekday() >= 5:
        print(f"weekend ({now_et:%Y-%m-%d %A} ET) -- nothing to check")
        return 0
    expected = now_et.strftime("%Y-%m-%d")
    hb_path = os.path.join(root, "heartbeat.json")
    try:
        with open(hb_path) as f:
            hb = json.load(f)
    except FileNotFoundError:
        print(f"STALE: no heartbeat.json in the mirror at all -- the VPS has "
              f"never pushed one (or the sync is wedged)")
        return 1
    except ValueError:
        print("STALE: heartbeat.json is corrupt (not JSON)")
        return 1
    today = str(hb.get("today", ""))
    if today != expected:
        print(f"STALE: mirror heartbeat is from {today or 'unknown'}, "
              f"expected {expected} -- the VPS is dead, the tick is broken, "
              f"or the sync stopped pushing")
        return 1
    if hb.get("dailyran") is not True:
        print(f"INCOMPLETE: heartbeat is from today but the EOD run never "
              f"completed (dailyran={hb.get('dailyran')!r}) -- check the VPS "
              f"logs; the day may be recorded as a gap")
        return 1
    print(f"fresh: {today}, EOD completed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--now-utc", default=None,
                    help="ISO timestamp override (tests)")
    args = ap.parse_args()
    return check(args.root, args.now_utc)


if __name__ == "__main__":
    sys.exit(main())
