"""D12 + D3: the missed-day alert must name the ACTUAL missed dates (not
today) and the REAL window close (23:30, not the stale 20:00), and a failed
send must retry on later ticks until one delivery succeeds -- suppression
keyed on a .gapalerted delivered-marker, never on the gap ledger's
idempotency (which lost the alarm forever after one dead-mailer evening)."""
import datetime as dt

from live.gaps import append_gap
from live.run_health import run_check


def _et(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm)


def _sender(ok):
    calls = []

    def send(subject, body, **kw):
        calls.append((subject, body))
        return ok
    return send, calls


def test_alert_names_missed_days_and_real_deadline(tmp_path):
    # 07-23 was missed and recorded; 07-24 completed. The 23:50 check on
    # 07-24 must alert about 07-23 -- not claim today was missed.
    g = str(tmp_path / "g.jsonl")
    append_gap("2026-07-23", "no_run", path=g)
    open(tmp_path / ".dailyran-2026-07-24", "w").close()
    send, calls = _sender(ok=True)
    run_check(_et(2026, 7, 24, 23, 50), str(tmp_path), g, send=send)
    assert len(calls) == 1
    subject, body = calls[0]
    assert "2026-07-23" in subject
    assert "2026-07-23" in body and "23:30" in body
    assert "2026-07-24" not in subject


def test_failed_send_retries_until_delivered(tmp_path):
    g = str(tmp_path / "g.jsonl")
    send_bad, calls_bad = _sender(ok=False)
    run_check(_et(2026, 7, 24, 23, 50), str(tmp_path), g, send=send_bad)
    assert len(calls_bad) == 1                      # tried, failed
    send_good, calls_good = _sender(ok=True)
    run_check(_et(2026, 7, 24, 23, 55), str(tmp_path), g, send=send_good)
    assert len(calls_good) == 1                     # retried on next tick
    send_third, calls_third = _sender(ok=True)
    run_check(_et(2026, 7, 24, 23, 58), str(tmp_path), g, send=send_third)
    assert calls_third == []                        # delivered -> suppressed


def test_multi_day_outage_one_email_naming_all(tmp_path):
    g = str(tmp_path / "g.jsonl")
    open(tmp_path / ".dailyran-2026-07-21", "w").close()
    send, calls = _sender(ok=True)
    run_check(_et(2026, 7, 24, 23, 50), str(tmp_path), g, send=send)
    assert len(calls) == 1
    subject, body = calls[0]
    assert "2026-07-22" in body and "2026-07-23" in body and "2026-07-24" in body


def test_quiet_day_sends_nothing(tmp_path):
    g = str(tmp_path / "g.jsonl")
    open(tmp_path / ".dailyran-2026-07-24", "w").close()
    send, calls = _sender(ok=True)
    r = run_check(_et(2026, 7, 24, 23, 50), str(tmp_path), g, send=send)
    assert calls == []
