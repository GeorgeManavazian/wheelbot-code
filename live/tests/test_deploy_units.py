"""D7 lint tests (declared weak: real timer semantics are Phase-F-only --
systemctl list-timers, stop/start trigger restoration, forced OnFailure)."""
import os

DEPLOY = os.path.join(os.path.dirname(__file__), "..", "..", "deploy")


def _read(name):
    with open(os.path.join(DEPLOY, name)) as f:
        return f.read()


def test_timer_is_wall_clock_anchored():
    t = _read("wheelbot.timer")
    assert "OnCalendar=*:0/5" in t
    assert "OnUnitActiveSec=" not in t, \
        "phase-slipping monotonic cadence must not come back"
    assert "Persistent=true" in t


def test_service_has_onfailure_alert():
    s = _read("wheelbot.service")
    assert "OnFailure=wheelbot-alert.service" in s


def test_alert_unit_calls_tick_failed():
    a = _read("wheelbot-alert.service")
    assert "run_notify.py tick-failed" in a
