"""Dead-man's switch. Every other alert needs a living process to fire; this
one fires BECAUSE nothing happened. It is the only check that catches the bot
being entirely dead -- the 2026-07-21 / 07-23 case, where no run occurred at
all on a weekday and nothing anywhere noticed for weeks.

Runs after the daily retry window closes (20:00 ET), so a day that merely
retried late is not misreported as missed."""
from __future__ import annotations

from live.paths import in_state
import os

LOGS_DIR = in_state("logs")
CUTOFF_HHMM = 2345          # 23:45 ET -- MUST be after the EOD retry window
                            # closes (23:30). Firing earlier would record a gap
                            # for a day that could still succeed on a later tick.
LOOKBACK_DAYS = 10          # cover a multi-day outage, not just today


def marker_path(date, logs_dir: str = LOGS_DIR) -> str:
    """The .dailyran-<date> completion marker the tick writes on a clean run."""
    return os.path.join(logs_dir, f".dailyran-{date}")


def check_day(now_et, logs_dir: str = LOGS_DIR, gaps_path=None,
              cutoff_hhmm: int = CUTOFF_HHMM, lookback_days: int = LOOKBACK_DAYS) -> str:
    """Decide whether recent trading days were missed, and record them.

    `now_et` is a naive datetime already resolved to Eastern.

    Scans BACK over `lookback_days` calendar days, not just today. The
    today-only version could not see a multi-day outage at all: if the VPS is
    down (or the timer stopped) Monday through Wednesday, no tick runs on those
    evenings to notice them, and Thursday's check only ever asked about
    Thursday. Three lost trading days would vanish with no gap record and no
    alert -- the precise failure this switch exists to catch.

    Weekends and days that completed (marker present) are skipped. Returns one
    of: "not_weekday", "too_early", "ok", "already_recorded", "gap_recorded".
    The caller alerts on "gap_recorded" ONLY -- re-alerting on
    "already_recorded" would email every 5 minutes until midnight."""
    import datetime as _dt
    from live.gaps import append_gap, GAPS_PATH
    if gaps_path is None:
        gaps_path = GAPS_PATH
    if now_et.weekday() >= 5:
        return "not_weekday"
    if now_et.hour * 100 + now_et.minute < cutoff_hhmm:
        return "too_early"

    today = now_et.date()
    recorded_any, saw_unrecorded = False, False
    for back in range(lookback_days, -1, -1):
        d = today - _dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        day = d.strftime("%Y-%m-%d")
        if os.path.exists(marker_path(day, logs_dir)):
            continue
        saw_unrecorded = True
        if append_gap(day, "no_run", path=gaps_path):
            recorded_any = True
    if recorded_any:
        return "gap_recorded"
    if saw_unrecorded:
        return "already_recorded"
    return "ok"
