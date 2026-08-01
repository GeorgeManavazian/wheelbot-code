"""D3 + D5: alert entrypoints must propagate DELIVERY into their exit codes
(the tick's marker-on-exit-0 idiom then retries undelivered alerts for free),
and the token nag must warn from 3 days of runway, escalate past expiry, and
alert on an unreadable token file instead of going silent.

The pre-fix defects: every subcommand returned 0 whether or not the email
went out (one revoked Gmail app password silenced the whole notification
system permanently and invisibly), the nag fired at 5.5 days only, an
expired token read "due in -1.0 days" forever, and a corrupt token file was
treated as "nothing to say" -- the bot-is-about-to-go-blind case."""
import json
import time

import live.run_notify as rn


def _token(tmp_path, age_days):
    p = tmp_path / "token.json"
    p.write_text(json.dumps(
        {"creation_timestamp": time.time() - age_days * 86400}))
    return str(p)


def _patch_send(monkeypatch, ok):
    calls = []

    def fake(subject, body, **kw):
        calls.append((subject, body))
        return ok
    monkeypatch.setattr(rn, "send_alert", fake)
    return calls


# ---- D3: delivery -> exit code ----

def test_sync_failed_nonzero_when_send_fails(monkeypatch):
    _patch_send(monkeypatch, ok=False)
    assert rn.sync_failed("2026-07-24") != 0


def test_sync_failed_zero_when_delivered(monkeypatch):
    _patch_send(monkeypatch, ok=True)
    assert rn.sync_failed("2026-07-24") == 0


def test_intraday_errors_nonzero_when_send_fails(monkeypatch, tmp_path):
    _patch_send(monkeypatch, ok=False)
    log = tmp_path / "i.log"
    log.write_text("ERROR boom")
    assert rn.intraday_errors("2026-07-24", str(log)) != 0


def test_token_nag_nonzero_when_send_fails(monkeypatch, tmp_path):
    _patch_send(monkeypatch, ok=False)
    assert rn.token_age(_token(tmp_path, age_days=6.5)) != 0


# ---- D5: nag thresholds ----

def test_warns_from_three_days_runway(monkeypatch, tmp_path):
    calls = _patch_send(monkeypatch, ok=True)
    assert rn.token_age(_token(tmp_path, age_days=4.5)) == 0  # 2.5 days left
    assert calls and "due in" in calls[0][0]


def test_quiet_above_three_days_runway(monkeypatch, tmp_path):
    calls = _patch_send(monkeypatch, ok=True)
    assert rn.token_age(_token(tmp_path, age_days=3.5)) == 1  # 3.5 days left
    assert not calls


def test_expired_token_gets_distinct_alert(monkeypatch, tmp_path):
    calls = _patch_send(monkeypatch, ok=True)
    assert rn.token_age(_token(tmp_path, age_days=8.0)) == 0
    assert calls and "EXPIRED" in calls[0][0]


def test_unreadable_token_file_alerts(monkeypatch, tmp_path):
    """A token file that EXISTS but yields no age is a different alarm, not
    silence -- the bot is about to go blind and nothing else will say so."""
    calls = _patch_send(monkeypatch, ok=True)
    p = tmp_path / "token.json"
    p.write_text(json.dumps({"creation_timestamp": "garbage"}))
    assert rn.token_age(str(p)) == 0
    assert calls and "unreadable" in calls[0][0].lower()


def test_absent_token_file_stays_quiet(monkeypatch, tmp_path):
    # no file yet (fresh install) is genuinely "nothing to say"
    calls = _patch_send(monkeypatch, ok=True)
    assert rn.token_age(str(tmp_path / "nope.json")) == 1
    assert not calls
