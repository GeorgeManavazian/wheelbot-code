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


def _deliver_now(subject: str, body: str, path: str = ALERTS_PATH) -> bool:
    """One delivery attempt. True on success, False on any failure (never
    raises). No spooling -- retry_spool uses this to avoid re-spooling its
    own failures."""
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


def _spool_path() -> str:
    # resolved at CALL time (state_root reads the env per call), so the
    # WHEELBOT_STATE_DIR import-time trap does not apply here
    from live.paths import in_state
    return in_state("alerts-failed.jsonl")


def send_alert(subject: str, body: str, path: str = ALERTS_PATH,
               spool_path: str = None) -> bool:
    """Send one alert. True on success, False on any failure (never raises).
    Subject is prefixed 'Wheel Bot: ' so inbox filters have a stable handle.

    D3: a failed send is appended to the spool (data/live/alerts-failed.jsonl,
    synced with the mirror) so a later tick can retry it and an undelivered
    backlog is VISIBLE instead of evaporating. Spooling itself never raises."""
    if _deliver_now(subject, body, path):
        return True
    try:
        import datetime as _dt
        sp = spool_path or _spool_path()
        os.makedirs(os.path.dirname(sp) or ".", exist_ok=True)
        rec = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
               "subject": subject, "body": body}
        with open(sp, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:                      # noqa: BLE001 -- deliberate
        print(f"[alert spool FAILED {type(e).__name__}: {e}] {subject}")
    return False


def retry_spool(spool_path: str = None, path: str = ALERTS_PATH,
                limit: int = 20):
    """Retry undelivered alerts, oldest first, at most `limit` per call.
    Returns (sent, remaining). Never raises; delivered lines are removed,
    failed and unattempted lines are kept in order."""
    sp = spool_path or _spool_path()
    try:
        with open(sp) as f:
            lines = [ln for ln in f if ln.strip()]
    except OSError:
        return 0, 0
    sent, kept, attempted = 0, [], 0
    for ln in lines:
        try:
            rec = json.loads(ln)
            subject, body = rec["subject"], rec["body"]
        except (ValueError, KeyError, TypeError):
            continue                     # corrupt line: drop, never wedge
        if attempted >= limit:
            kept.append(ln)
            continue
        attempted += 1
        if _deliver_now(subject, body, path):
            sent += 1
        else:
            kept.append(ln)
    try:
        with open(sp, "w") as f:
            f.writelines(kept)
    except OSError as e:
        print(f"[alert spool rewrite FAILED {type(e).__name__}: {e}]")
    return sent, len(kept)
