"""Sandbox harness for scripts/wheelbot_iv_tick.sh (2026-08-07).

Same shape as test_tick_script.py: the REAL script runs against a throwaway
repo dir with a stub `.venv-live/bin/python` that journals every invocation and
returns scripted exit codes per entrypoint. The WHEELBOT_* env hooks are inert
in production (unset -> deployed defaults).

These tests moved out of test_tick_script.py when the accrual moved out of the
tick. The reason it moved is worth keeping in front of whoever edits this file:
wheelbot.service is Type=oneshot on a 5-minute OnCalendar timer, so a tick that
runs long absorbs the next trigger. Intraday (~seconds) + chain snapshot
(~7 min) + accrual (~5 min) pushed the intraday take-profit manager from 15:25
and 15:30 out to ~15:35, on every accrual retry tick. Its own timer runs the
pull concurrently instead, so the accrual cannot delay a trading decision.

The gate that is genuinely load-bearing -- and the reason most of these tests
exist -- is `.chainsnap-$TODAY`: the 547-chain accrual pull must never take the
window or the API budget from the ~12-chain trading pull the day's decision
depends on.
"""
import os
import stat
import subprocess
from pathlib import Path

import pytest

IVTICK = str(Path(__file__).resolve().parents[2] / "scripts"
             / "wheelbot_iv_tick.sh")

SHIM = """#!/usr/bin/env bash
J="${TICK_JOURNAL:?}"
printf '%s\\n' "$*" >> "$J"
key="${1:-inline}"
if [ "$key" = "-c" ]; then key="inline_sync"; fi
key=$(basename "$key" .py)
key=${key//[^a-zA-Z0-9_]/_}
outvar="TICK_OUT_${key}"
rcvar="TICK_RC_${key}"
[ -n "${!outvar:-}" ] && printf '%s\\n' "${!outvar}"
exit "${!rcvar:-0}"
"""


class Sandbox:
    def __init__(self, root: Path):
        self.root = root
        self.logs = root / "data" / "live" / "logs"
        self.logs.mkdir(parents=True)
        shim = root / ".venv-live" / "bin" / "python"
        shim.parent.mkdir(parents=True)
        shim.write_text(SHIM)
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
        self.journal = root / "journal.txt"
        self.journal.write_text("")

    def tick(self, dow="5", hm="1530", today="2026-07-17", env=None):
        e = os.environ.copy()
        e.update({"WHEELBOT_REPO": str(self.root),
                  "WHEELBOT_FAKE_DOW": dow,
                  "WHEELBOT_FAKE_HM": hm,
                  "WHEELBOT_FAKE_TODAY": today,
                  "TICK_JOURNAL": str(self.journal)})
        e.update(env or {})
        return subprocess.run(["bash", IVTICK], env=e, capture_output=True,
                              text=True, timeout=60)

    def calls(self):
        return self.journal.read_text().splitlines()


@pytest.fixture
def sb(tmp_path):
    return Sandbox(tmp_path)


def _ran(calls, script):
    return any(script in c for c in calls)


def test_iv_accrual_does_not_run_before_the_chain_snapshot(sb):
    """No .chainsnap marker => the trading pull has not finished. 547 chains
    must not compete with it for the window or the API budget."""
    sb.tick(dow="5", hm="1530", today="2026-07-17")
    assert not _ran(sb.calls(), "run_iv_accrual.py"), \
        "547-chain pull must not run while the trading snapshot is unfinished"
    assert not (sb.logs / ".ivaccrual-2026-07-17").exists()


def test_iv_accrual_runs_once_the_chain_snapshot_marker_exists(sb):
    (sb.logs / ".chainsnap-2026-07-17").touch()
    sb.tick(dow="5", hm="1530", today="2026-07-17")
    assert _ran(sb.calls(), "run_iv_accrual.py")
    assert (sb.logs / ".ivaccrual-2026-07-17").exists()


def test_iv_accrual_marker_is_written_only_on_success(sb):
    (sb.logs / ".chainsnap-2026-07-17").touch()
    sb.tick(dow="5", hm="1530", today="2026-07-17",
            env={"TICK_RC_run_iv_accrual": "1"})
    assert not (sb.logs / ".ivaccrual-2026-07-17").exists(), \
        "a failed pull must retry on every remaining in-window tick"
    sb.tick(dow="5", hm="1535", today="2026-07-17")
    assert (sb.logs / ".ivaccrual-2026-07-17").exists()


def test_iv_accrual_failure_does_not_fail_the_unit(sb):
    """An IV outage must never alert-storm or look like a failed unit --
    wheelbot-iv.service carries no OnFailure=, and this is the other half of
    that contract."""
    (sb.logs / ".chainsnap-2026-07-17").touch()
    r = sb.tick(dow="5", hm="1530", today="2026-07-17",
                env={"TICK_RC_run_iv_accrual": "1"})
    assert r.returncode == 0


def test_iv_accrual_does_not_rerun_once_done(sb):
    (sb.logs / ".chainsnap-2026-07-17").touch()
    (sb.logs / ".ivaccrual-2026-07-17").touch()
    sb.tick(dow="5", hm="1530", today="2026-07-17")
    assert not _ran(sb.calls(), "run_iv_accrual.py")


def test_iv_accrual_does_not_run_on_a_weekend(sb):
    (sb.logs / ".chainsnap-2026-07-18").touch()
    sb.tick(dow="6", hm="1530", today="2026-07-18")
    assert not _ran(sb.calls(), "run_iv_accrual.py")


def test_iv_accrual_does_not_run_outside_the_window(sb):
    (sb.logs / ".chainsnap-2026-07-17").touch()
    sb.tick(dow="5", hm="1519", today="2026-07-17")
    sb.tick(dow="5", hm="1600", today="2026-07-17")
    assert not _ran(sb.calls(), "run_iv_accrual.py")


def test_this_unit_runs_nothing_but_the_accrual(sb):
    """It is a separate systemd unit precisely so it carries no trading-path
    work; nothing here may creep back into the serialized tick's job list."""
    (sb.logs / ".chainsnap-2026-07-17").touch()
    sb.tick(dow="5", hm="1530", today="2026-07-17")
    assert sb.calls() == ["live/run_iv_accrual.py"], sb.calls()
