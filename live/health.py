"""Dead-man's switch. Every other alert needs a living process to fire; this
one fires BECAUSE nothing happened. It is the only check that catches the bot
being entirely dead -- the 2026-07-21 / 07-23 case, where no run occurred at
all on a weekday and nothing anywhere noticed for weeks.

Runs after the daily retry window closes (23:30 ET), so a day that merely
retried late is not misreported as missed."""
from __future__ import annotations

from live.paths import in_state
import os
import re

LOGS_DIR = in_state("logs")
CUTOFF_HHMM = 2345          # 23:45 ET -- MUST be after the EOD retry window
                            # closes (23:30). Firing earlier would record a gap
                            # for a day that could still succeed on a later tick.
LOOKBACK_DAYS = 10          # fresh-install fallback ONLY (D4): with no marker
                            # and no gap anywhere, bound the first scan.

# D12: the one place the window-close time lives for MESSAGES. The tick's gate
# is `[ "$HM" -le 2330 ]` in scripts/wheelbot_tick.sh -- shell cannot import
# this, so the two are cross-pinned by comment: change one, change both.
EOD_WINDOW_CLOSE = "23:30"


def marker_path(date, logs_dir: str = LOGS_DIR) -> str:
    """The .dailyran-<date> completion marker the tick writes on a clean run."""
    return os.path.join(logs_dir, f".dailyran-{date}")


def _scan_start(logs_dir, gaps_path, today, lookback_days):
    """D4: the scan starts the day AFTER the last evidence of life -- the
    newest completion marker or the newest recorded gap -- instead of a fixed
    window. The fixed 10-day window recorded only the newest 10 days of a
    longer outage: older missed days returned to "ok" and were never gapped,
    under-disclosing exactly when the failure was worst. Falls back to the
    bounded window only when both sources are empty (fresh install)."""
    import datetime as _dt
    from live.gaps import recorded_dates
    dates = set(recorded_dates(gaps_path))
    try:
        for name in os.listdir(logs_dir):
            m = re.fullmatch(r"\.dailyran-(\d{4}-\d{2}-\d{2})", name)
            if m:
                dates.add(m.group(1))
    except OSError:
        pass
    fallback = today - _dt.timedelta(days=lookback_days)
    # newest PARSEABLE evidence (skeptic F9: a regex-shaped but invalid name
    # like 2026-99-99 string-sorts above real dates; a blind max() would then
    # silently degrade the whole scan to the fallback window)
    last = None
    for cand in sorted(dates, reverse=True):
        try:
            last = _dt.date.fromisoformat(cand)
            break
        except ValueError:
            continue
    if last is None:
        return fallback
    # never past today (a future-dated marker from clock skew must not blind
    # the scan to today), never a negative scan
    return min(last + _dt.timedelta(days=1), today)


def check_day(now_et, logs_dir: str = LOGS_DIR, gaps_path=None,
              cutoff_hhmm: int = CUTOFF_HHMM, lookback_days: int = LOOKBACK_DAYS):
    """Decide whether trading days since the last evidence of life were
    missed, and record them.

    `now_et` is a naive datetime already resolved to Eastern.

    Returns (status, missed_days): status is one of "not_weekday",
    "too_early", "ok", "already_recorded", "gap_recorded"; missed_days lists
    every scanned weekday with no completion marker (D12: the caller names
    THESE days in the alert, never just "today"). A recorded gap counts as
    evidence of life (the switch was alive to record it), so the next scan
    starts after it -- alert retry does NOT key on this status; run_health's
    unalerted-gaps scan owns delivery with .gapalerted markers."""
    import datetime as _dt
    from live.gaps import append_gap, GAPS_PATH
    if gaps_path is None:
        gaps_path = GAPS_PATH
    if now_et.weekday() >= 5:
        return "not_weekday", []
    if now_et.hour * 100 + now_et.minute < cutoff_hhmm:
        return "too_early", []

    today = now_et.date()
    d = _scan_start(logs_dir, gaps_path, today, lookback_days)
    recorded_any, missed = False, []
    while d <= today:
        if d.weekday() < 5:
            day = d.strftime("%Y-%m-%d")
            if not os.path.exists(marker_path(day, logs_dir)):
                missed.append(day)
                if append_gap(day, "no_run", path=gaps_path):
                    recorded_any = True
        d += _dt.timedelta(days=1)
    if recorded_any:
        return "gap_recorded", missed
    if missed:
        return "already_recorded", missed
    return "ok", missed


# D1: the intraday manager must have ticked close to the bell. Its final
# in-hours pass lands at 15:55-16:00; a last stamp before this means it died
# mid-session (the audited 4-deaths pattern, RTH coverage 70.2%).
INTRADAY_ALIVE_HHMM = "15:45"


def intraday_last_tick(logs_dir, day):
    """Last 'intraday HH:MM' stamp in the day's intraday log, or None when
    the log is absent/empty (the manager never ticked at all)."""
    last = None
    try:
        with open(os.path.join(logs_dir, f"intraday-{day}.log")) as f:
            for line in f:
                m = re.search(r"intraday (\d{2}:\d{2})", line)
                if m:
                    last = m.group(1)
    except OSError:
        return None
    return last


def unalerted_gaps(gaps_path, logs_dir, today, within_days: int = 14):
    """D3/D12: recorded no-run gaps whose alert has NOT been delivered yet
    (no .gapalerted-<day> marker). One failed send used to lose the missed-day
    alarm forever -- append_gap's idempotency suppressed the re-alert. The
    delivered marker, not the ledger, now owns suppression. Bounded to the
    last `within_days` days so ancient disclosed gaps do not re-email."""
    import datetime as _dt
    from live.gaps import gap_summary
    out = []
    for rec in gap_summary(gaps_path)["records"]:
        if rec.get("reason") != "no_run":
            continue
        day = str(rec["date"])
        try:
            d = _dt.date.fromisoformat(day)
        except ValueError:
            continue
        if (today - d).days > within_days:
            continue
        if not os.path.exists(os.path.join(logs_dir, f".gapalerted-{day}")):
            out.append(day)
    return sorted(out)
