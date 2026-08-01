"""Alert entrypoints for the tick, kept in Python rather than shell one-liners.

The tick dispatcher used to inline these as `python -c "..."` strings. That is
where quoting bugs and unreadable logic live, and this is alerting code -- the
thing that has to work on the worst day. Each subcommand is a tiny, testable
function with an exit code the shell can act on.

  python live/run_notify.py token-age <token.json>
  python live/run_notify.py sync-failed <YYYY-MM-DD>
  python live/run_notify.py intraday-errors <YYYY-MM-DD> <logfile>
  python live/run_notify.py retry-spool
"""
from __future__ import annotations
import os
import sys

from live.alerts import send_alert, retry_spool
from live.tokenage import token_age_days, REFRESH_TOKEN_LIFETIME_DAYS

# D5 (owner 2026-08-01): warn daily from 3 days of runway. The old 5.5-day
# single-shot nag, gated to weekday evenings by the tick, left two of fourteen
# login times with ZERO warning before the 7-day lapse.
WARN_LEFT_DAYS = 3.0

# D3 (owner 2026-08-01): every subcommand exits 0 ONLY when the alert was
# DELIVERED. The tick writes its once-per-day suppression marker on exit 0, so
# an undelivered alert retries on the next tick for free. The old code
# returned 0 unconditionally -- one revoked Gmail app password silenced the
# entire notification system permanently and invisibly.

SSH_HINT = (
    "  ssh -i ~/.ssh/wheelbot.key ubuntu@129.80.185.142\n"
    "  cd /home/ubuntu/etf-bot && PYTHONPATH=. .venv-live/bin/python "
    "scripts/schwab/schwab_login.py"
)


def token_age(token_path: str) -> int:
    """Warn while the Schwab REFRESH token has <= 3 days of runway; escalate
    with a distinct subject once it has lapsed; alert when the token file
    exists but is unreadable (the about-to-go-blind case the old None-check
    silently swallowed). Exit 0 = alert DELIVERED (caller marks the day done),
    1 = nothing to say or delivery failed (caller retries next tick)."""
    age = token_age_days(token_path)
    if age is None:
        if not os.path.exists(token_path):
            return 1                    # fresh install: genuinely nothing to say
        ok = send_alert(
            "Schwab token file unreadable",
            f"{token_path} exists but its creation_timestamp cannot be read.\n"
            f"The 7-day expiry clock is INVISIBLE: the bot may go blind with "
            f"no further warning.\n\nRe-run the login on the VPS:\n{SSH_HINT}",
        )
        return 0 if ok else 1
    left = REFRESH_TOKEN_LIFETIME_DAYS - age
    if left > WARN_LEFT_DAYS:
        return 1
    if left <= 0:
        ok = send_alert(
            "Schwab token EXPIRED -- bot is blind",
            f"The refresh token lapsed {-left:.1f} days ago. Every Schwab "
            f"pull fails until you log in again; entries, exits and snapshots "
            f"are NOT happening.\n\nRe-run the login on the VPS:\n{SSH_HINT}",
        )
    else:
        ok = send_alert(
            f"Schwab login due in {left:.1f} days",
            f"The Schwab refresh token is {age:.1f} days old and lapses at "
            f"{REFRESH_TOKEN_LIFETIME_DAYS} days.\n\n"
            f"Re-run the login on the VPS:\n{SSH_HINT}\n\n"
            f"If it lapses, every Schwab pull fails and the bot goes blind.",
        )
    if ok:
        print(f"NAGGED age={age:.2f} left={left:.2f}")
        return 0
    return 1


def sync_failed(day: str) -> int:
    ok = send_alert(
        f"state sync FAILED {day}",
        f"The {day} EOD run SUCCEEDED, but pushing state to GitHub failed.\n\n"
        f"The bot keeps trading and the VPS state is authoritative, so no "
        f"trading data is lost. But your dashboard reads the synced mirror, so "
        f"it will show STALE numbers until this recovers.\n\n"
        f"Likely causes: the GitHub PAT expired or lost permissions, or GitHub "
        f"was unreachable. Check the run log on the VPS.",
    )
    return 0 if ok else 1


def intraday_errors(day: str, log_path: str) -> int:
    tail = ""
    try:
        with open(log_path) as f:
            tail = f.read()[-1500:]
    except OSError:
        tail = "(could not read the intraday log)"
    ok = send_alert(
        f"intraday exit manager erroring {day}",
        f"run_intraday.py logged errors on {day}. Take-profit exits may not be "
        f"firing, which looks IDENTICAL to a quiet day in the logs -- that is "
        f"why this alert exists.\n\nRecent log:\n\n{tail}",
    )
    return 0 if ok else 1


def tick_failed(logs_dir: str = None, day: str = None) -> int:
    """D7: fired by systemd's OnFailure= when a tick exits nonzero. Once per
    day (delivered marker); the tick's own per-failure alerts carry the
    detail -- this is the backstop for failures those never reached."""
    import datetime as _dt
    from zoneinfo import ZoneInfo
    from live.paths import in_state
    if day is None:
        day = _dt.datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    ldir = logs_dir or in_state("logs")
    marker = os.path.join(ldir, f".tickfailalerted-{day}")
    if os.path.exists(marker):
        return 0
    ok = send_alert(
        f"tick FAILED {day}",
        f"systemd reported wheelbot.service failed on {day} (nonzero tick "
        f"exit). One or more blocks -- EOD run, intraday manager, sync, "
        f"health -- failed this tick. Specific alerts should name the block; "
        f"if none arrived, the failure happened before alerting could run.\n\n"
        f"Check tick.log and the day's logs on the VPS.",
    )
    if ok:
        try:
            os.makedirs(ldir, exist_ok=True)
            open(marker, "w").close()
        except OSError:
            pass
        return 0
    return 1


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "token-age":
        return token_age(argv[2] if len(argv) > 2
                         else os.path.expanduser("~/.schwab/token.json"))
    if cmd == "sync-failed":
        return sync_failed(argv[2] if len(argv) > 2 else "unknown-date")
    if cmd == "intraday-errors":
        return intraday_errors(argv[2] if len(argv) > 2 else "unknown-date",
                               argv[3] if len(argv) > 3 else "")
    if cmd == "tick-failed":
        return tick_failed()
    if cmd == "retry-spool":
        sent, remaining = retry_spool()
        if sent or remaining:
            print(f"alert spool: {sent} sent, {remaining} remaining")
        return 0 if remaining == 0 else 1
    print(f"unknown subcommand: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
