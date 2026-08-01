"""Sandbox harness for scripts/wheelbot_tick.sh (Group D batch 0 enabler).

Runs the REAL tick script against a throwaway repo dir: a stub
`.venv-live/bin/python` records every invocation to a journal and returns
scripted exit codes / stdout per entrypoint. The WHEELBOT_* env hooks are
inert in production (unset -> deployed defaults).

Smoke tests here pin CURRENT behavior only; Group D red tests build on this
harness."""
import os
import stat
import subprocess
from pathlib import Path

import pytest

TICK = str(Path(__file__).resolve().parents[2] / "scripts" / "wheelbot_tick.sh")

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

    def tick(self, dow="5", hm="1700", today="2026-07-24", env=None):
        e = os.environ.copy()
        e.update({"WHEELBOT_REPO": str(self.root),
                  "WHEELBOT_FAKE_DOW": dow,
                  "WHEELBOT_FAKE_HM": hm,
                  "WHEELBOT_FAKE_TODAY": today,
                  "WHEELBOT_TOKEN_PATH": str(self.root / "token.json"),
                  "TICK_JOURNAL": str(self.journal)})
        e.update(env or {})
        return subprocess.run(["bash", TICK], env=e, capture_output=True,
                              text=True, timeout=60)

    def calls(self):
        return self.journal.read_text().splitlines()


@pytest.fixture
def sb(tmp_path):
    return Sandbox(tmp_path)


def test_eod_success_writes_marker_and_syncs(sb):
    r = sb.tick(dow="5", hm="1700")
    assert r.returncode == 0
    assert any("run_daily.py" in c for c in sb.calls())
    assert (sb.logs / ".dailyran-2026-07-24").exists()
    assert any(c.startswith("-c") and "sync_state" in c for c in sb.calls())


def test_eod_failure_no_marker(sb):
    r = sb.tick(env={"TICK_RC_run_daily": "1"})
    assert not (sb.logs / ".dailyran-2026-07-24").exists()
    assert not any("sync_state" in c for c in sb.calls())
    assert r.returncode == 0  # pinned CURRENT behavior; D9 flips this


def test_weekend_runs_nothing(sb):
    sb.tick(dow="6", hm="1700")
    assert sb.calls() == []


def test_intraday_error_alerts_once(sb):
    env = {"TICK_OUT_run_intraday": "acct ERROR boom", "TICK_RC_run_intraday": "0"}
    sb.tick(dow="5", hm="1000", env=env)
    assert any("intraday-errors" in c for c in sb.calls())
    n_first = sum("intraday-errors" in c for c in sb.calls())
    assert n_first == 1
    sb.tick(dow="5", hm="1005", env=env)
    assert sum("intraday-errors" in c for c in sb.calls()) == 1  # marker held


def test_snapshot_window_marker_only_on_success(sb):
    sb.tick(dow="5", hm="1530", env={"TICK_RC_run_chain_snapshot": "1"})
    assert not (sb.logs / ".chainsnap-2026-07-24").exists()
    sb.tick(dow="5", hm="1535")
    assert (sb.logs / ".chainsnap-2026-07-24").exists()


def test_health_runs_after_cutoff(sb):
    sb.tick(dow="5", hm="2350")
    assert any("run_health.py" in c for c in sb.calls())
