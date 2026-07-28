"""Dead-man's switch. Every other alert needs a living process to fire; this
one fires BECAUSE nothing happened. It is the only check that catches the bot
being entirely dead -- the 2026-07-21 / 07-23 case, where no run occurred at
all on a weekday and nothing anywhere noticed for weeks.

Runs after the daily retry window closes (20:00 ET), so a day that merely
retried late is not misreported as missed."""
from __future__ import annotations
import os

LOGS_DIR = "data/live/logs"
CUTOFF_HHMM = 2015          # 20:15 ET -- after the 17:00-20:00 retry window


def marker_path(date, logs_dir: str = LOGS_DIR) -> str:
    """The .dailyran-<date> completion marker the tick writes on a clean run."""
    return os.path.join(logs_dir, f".dailyran-{date}")


def check_day(now_et, logs_dir: str = LOGS_DIR, gaps_path=None,
              cutoff_hhmm: int = CUTOFF_HHMM) -> str:
    """Decide whether `now_et`'s date is a missed trading day, and record it.

    `now_et` is a naive datetime already resolved to Eastern.

    Returns one of: "not_weekday", "too_early", "ok", "already_recorded",
    "gap_recorded". The caller alerts on "gap_recorded" ONLY -- re-alerting on
    "already_recorded" would email every 5 minutes until midnight."""
    from live.gaps import append_gap, GAPS_PATH
    if gaps_path is None:
        gaps_path = GAPS_PATH
    if now_et.weekday() >= 5:
        return "not_weekday"
    if now_et.hour * 100 + now_et.minute < cutoff_hhmm:
        return "too_early"
    day = now_et.strftime("%Y-%m-%d")
    if os.path.exists(marker_path(day, logs_dir)):
        return "ok"
    return "gap_recorded" if append_gap(day, "no_run", path=gaps_path) \
        else "already_recorded"
