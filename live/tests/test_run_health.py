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
    (tmp_path / "intraday-2026-07-24.log").write_text(
        "intraday 15:55 ET — 0 TP close(s)\n")
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
    (tmp_path / "intraday-2026-07-24.log").write_text(
        "intraday 15:55 ET — 0 TP close(s)\n")
    send, calls = _sender(ok=True)
    r = run_check(_et(2026, 7, 24, 23, 50), str(tmp_path), g, send=send)
    assert calls == []


# ---- D1: nightly post-hoc intraday liveness ----

def _mk(tmp_path, day="2026-07-24", log_lines=None, marker=True):
    if marker:
        open(tmp_path / f".dailyran-{day}", "w").close()
    if log_lines is not None:
        (tmp_path / f"intraday-{day}.log").write_text("\n".join(log_lines) + "\n")


def test_intraday_death_midsession_alerts_same_night(tmp_path):
    """The intraday manager died 4x mid-session with zero signal (RTH
    coverage 70.2%). The nightly check must notice the last tick stamp is
    hours before the close and say so."""
    g = str(tmp_path / "g.jsonl")
    _mk(tmp_path, log_lines=["intraday 09:35 ET — 0 TP close(s) across 25 accounts",
                             "intraday 14:00 ET — 1 TP close(s) across 25 accounts"])
    send, calls = _sender(ok=True)
    run_check(_et(2026, 7, 24, 23, 50), str(tmp_path), g, send=send)
    assert len(calls) == 1
    subject, body = calls[0]
    assert "intraday" in subject.lower()
    assert "14:00" in body


def test_intraday_alert_delivered_marker_suppresses(tmp_path):
    g = str(tmp_path / "g.jsonl")
    _mk(tmp_path, log_lines=["intraday 10:00 ET — 0 TP close(s)"])
    send, calls = _sender(ok=True)
    run_check(_et(2026, 7, 24, 23, 50), str(tmp_path), g, send=send)
    send2, calls2 = _sender(ok=True)
    run_check(_et(2026, 7, 24, 23, 55), str(tmp_path), g, send=send2)
    assert len(calls) == 1 and calls2 == []


def test_intraday_alive_to_close_stays_quiet(tmp_path):
    g = str(tmp_path / "g.jsonl")
    _mk(tmp_path, log_lines=["intraday 09:35 ET — 0 TP close(s)",
                             "intraday 15:55 ET — 0 TP close(s)"])
    send, calls = _sender(ok=True)
    run_check(_et(2026, 7, 24, 23, 50), str(tmp_path), g, send=send)
    assert calls == []


def test_no_intraday_log_on_a_completed_day_alerts(tmp_path):
    # EOD completed (VPS alive in the evening) but the intraday manager
    # never ticked once -- the never-started case
    g = str(tmp_path / "g.jsonl")
    _mk(tmp_path, log_lines=None)
    send, calls = _sender(ok=True)
    run_check(_et(2026, 7, 24, 23, 50), str(tmp_path), g, send=send)
    assert len(calls) == 1
    assert "intraday" in calls[0][0].lower()


def test_vps_down_day_defers_to_missed_day_alert(tmp_path):
    # no .dailyran marker: the missed-day alert owns this; no intraday email
    g = str(tmp_path / "g.jsonl")
    send, calls = _sender(ok=True)
    run_check(_et(2026, 7, 24, 23, 50), str(tmp_path), g, send=send)
    intraday_alerts = [c for c in calls if "intraday" in c[0].lower()]
    assert intraday_alerts == []


# ---- Group D skeptic F3: undelivered alerts must surface in the exit code ----

def test_undelivered_alert_reported_in_return(tmp_path):
    """'Gap found, alert undelivered' was an exit-0 night -- D9's FAIL and
    D7's OnFailure backstop never saw it. run_check must report undelivered
    attempts so main() can exit nonzero."""
    g = str(tmp_path / "g.jsonl")
    send_bad, _ = _sender(ok=False)
    status, undelivered = run_check(_et(2026, 7, 24, 23, 50), str(tmp_path), g,
                                    send=send_bad)
    assert undelivered > 0
    send_good, _ = _sender(ok=True)
    status, undelivered = run_check(_et(2026, 7, 24, 23, 55), str(tmp_path), g,
                                    send=send_good)
    assert undelivered == 0
