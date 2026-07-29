"""Alert entrypoints for the tick, kept in Python rather than shell one-liners.

The tick dispatcher used to inline these as `python -c "..."` strings. That is
where quoting bugs and unreadable logic live, and this is alerting code -- the
thing that has to work on the worst day. Each subcommand is a tiny, testable
function with an exit code the shell can act on.

  python live/run_notify.py token-age <token.json>
  python live/run_notify.py sync-failed <YYYY-MM-DD>
  python live/run_notify.py intraday-errors <YYYY-MM-DD> <logfile>
"""
from __future__ import annotations
import os
import sys

from live.alerts import send_alert
from live.tokenage import token_age_days, REFRESH_TOKEN_LIFETIME_DAYS

WARN_AT_DAYS = 5.5          # ~1.5 days of runway before the 7-day lapse

SSH_HINT = (
    "  ssh -i ~/.ssh/wheelbot.key ubuntu@129.80.185.142\n"
    "  cd /home/ubuntu/etf-bot && PYTHONPATH=. .venv-live/bin/python "
    "scripts/schwab/schwab_login.py"
)


def token_age(token_path: str) -> int:
    """Warn when the Schwab REFRESH token is close to its 7-day lapse.
    Exit 0 = warned (caller should mark it done), 1 = nothing to say."""
    age = token_age_days(token_path)
    if age is None or age < WARN_AT_DAYS:
        return 1
    left = REFRESH_TOKEN_LIFETIME_DAYS - age
    send_alert(
        f"Schwab login due in {left:.1f} days",
        f"The Schwab refresh token is {age:.1f} days old and lapses at "
        f"{REFRESH_TOKEN_LIFETIME_DAYS} days.\n\n"
        f"Re-run the login on the VPS:\n{SSH_HINT}\n\n"
        f"If it lapses, every Schwab pull fails and the bot goes blind.",
    )
    print(f"NAGGED age={age:.2f} left={left:.2f}")
    return 0


def sync_failed(day: str) -> int:
    send_alert(
        f"state sync FAILED {day}",
        f"The {day} EOD run SUCCEEDED, but pushing state to GitHub failed.\n\n"
        f"The bot keeps trading and the VPS state is authoritative, so no "
        f"trading data is lost. But your dashboard reads the synced mirror, so "
        f"it will show STALE numbers until this recovers.\n\n"
        f"Likely causes: the GitHub PAT expired or lost permissions, or GitHub "
        f"was unreachable. Check the run log on the VPS.",
    )
    return 0


def intraday_errors(day: str, log_path: str) -> int:
    tail = ""
    try:
        with open(log_path) as f:
            tail = f.read()[-1500:]
    except OSError:
        tail = "(could not read the intraday log)"
    send_alert(
        f"intraday exit manager erroring {day}",
        f"run_intraday.py logged errors on {day}. Take-profit exits may not be "
        f"firing, which looks IDENTICAL to a quiet day in the logs -- that is "
        f"why this alert exists.\n\nRecent log:\n\n{tail}",
    )
    return 0


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
    print(f"unknown subcommand: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
