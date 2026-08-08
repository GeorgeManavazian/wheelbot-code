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


# ---- IV accrual units (2026-08-07) ---------------------------------------
# The accrual moved out of the tick and onto its own timer: wheelbot.service is
# Type=oneshot on a 5-minute OnCalendar, so a ~5 min accrual pull inside the
# tick pushed the intraday take-profit manager from 15:25/15:30 to ~15:35.

def test_iv_timer_is_wall_clock_anchored():
    t = _read("wheelbot-iv.timer")
    assert "OnCalendar=*:0/5" in t
    assert "OnUnitActiveSec=" not in t, \
        "phase-slipping monotonic cadence must not come back"
    assert "Persistent=true" in t
    assert "WantedBy=timers.target" in t


def test_iv_service_has_NO_onfailure():
    """The load-bearing one. The trading unit alerts on failure; this one must
    not, or a Schwab hiccup during data accrual pages the owner -- and the
    script exits 0 regardless, so the alert would be pure noise."""
    s = _read("wheelbot-iv.service")
    directives = [l for l in s.splitlines() if not l.lstrip().startswith("#")]
    assert not any(l.startswith("OnFailure=") for l in directives), \
        "an IV outage must never page the owner"


def test_iv_service_runs_the_iv_script_with_room_to_finish():
    s = _read("wheelbot-iv.service")
    assert "Type=oneshot" in s
    assert "scripts/wheelbot_iv_tick.sh" in s
    assert "wheelbot_tick.sh" not in s.replace("wheelbot_iv_tick.sh", ""), \
        "this unit must not fire the trading tick"
    timeout = [l for l in s.splitlines() if l.startswith("TimeoutStartSec=")]
    assert timeout, "a 547-ticker pull needs an explicit, generous timeout"
    assert int(timeout[0].split("=")[1]) >= 900
