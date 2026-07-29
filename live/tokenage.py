"""Age of the Schwab REFRESH token.

Do not use token.json's mtime for this. schwab-py rewrites that file every time
it refreshes the short-lived ACCESS token -- which happens on essentially every
run -- so the file's mtime is never more than minutes old. The pre-2026-07-29
nag keyed on mtime and could therefore never fire: the bot would have gone blind
at the 7-day refresh-token expiry with no warning at all.

schwab-py stores the real issue time as a top-level `creation_timestamp` (unix
seconds) and does NOT touch it on an access-token refresh. That is the field
that actually tracks the 7-day clock."""
from __future__ import annotations
import json
import time

REFRESH_TOKEN_LIFETIME_DAYS = 7


def token_age_days(path: str):
    """Age of the refresh token in days, or None if it cannot be determined.
    None means "do not warn" -- a malformed token file is a different alarm."""
    try:
        with open(path) as f:
            d = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None
    ts = d.get("creation_timestamp") if isinstance(d, dict) else None
    try:
        return (time.time() - float(ts)) / 86400.0
    except (TypeError, ValueError):
        return None


def days_until_expiry(path: str):
    """Days left before the refresh token lapses, or None if unknown."""
    age = token_age_days(path)
    return None if age is None else REFRESH_TOKEN_LIFETIME_DAYS - age
