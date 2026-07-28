"""Email alerts for the unattended bot. Config at ~/.wheelbot/alerts.json
(chmod 600, outside the repo -- same pattern as ~/.schwab/config.json).

Replaces the `osascript` desktop notifications in the old wheelbot_daily.sh,
which only ever worked when the owner was sitting in front of the Mac -- the
exact assumption the VPS migration removes.

INVARIANT: send_alert never raises. A dead mailer must never be the reason a
trading run aborts; it degrades to a printed line in the run log."""
from __future__ import annotations
import json
import os
import smtplib
from email.message import EmailMessage

ALERTS_PATH = os.path.expanduser("~/.wheelbot/alerts.json")


def load_alert_config(path: str = ALERTS_PATH):
    """The alert config dict, or None when absent/unreadable/malformed."""
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None
    return cfg if isinstance(cfg, dict) else None


def send_alert(subject: str, body: str, path: str = ALERTS_PATH) -> bool:
    """Send one alert. True on success, False on any failure (never raises).
    Subject is prefixed 'Wheel Bot: ' so inbox filters have a stable handle."""
    cfg = load_alert_config(path)
    if cfg is None:
        print(f"[alert skipped -- no config at {path}] {subject}")
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = f"Wheel Bot: {subject}"
        msg["From"] = cfg["from_addr"]
        msg["To"] = cfg["to_addr"]
        msg.set_content(body)
        srv = smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=30)
        srv.starttls()
        srv.login(cfg["username"], cfg["password"])
        srv.send_message(msg)
        srv.quit()
        return True
    except Exception as e:                      # noqa: BLE001 -- deliberate
        print(f"[alert FAILED {type(e).__name__}: {e}] {subject}")
        return False
