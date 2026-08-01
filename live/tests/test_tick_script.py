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
    assert r.returncode != 0  # D9: systemd must SEE the failure


def test_weekend_runs_only_spool_retry(sb):
    # Weekend = no trading-path work. retry-spool and token-age are both
    # legitimate any-day maintenance (D5 made the nag every-tick; D5b made
    # it run even with the token file absent -- which is why this sandbox,
    # which writes no token.json, now sees the token-age call too).
    sb.tick(dow="6", hm="1700")
    for c in sb.calls():
        assert ("retry-spool" in c or "token-age" in c), \
            f"weekend tick ran trading-path work: {c}"
    assert not any(x in c for c in sb.calls()
                   for x in ("run_daily", "run_intraday", "run_chain_snapshot"))


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


# ---- Group D batch 3: the tick rewrite ----

def test_health_runs_first(sb):
    sb.tick(dow="5", hm="2350")
    py_calls = [c for c in sb.calls()]
    assert py_calls and "run_health.py" in py_calls[0], \
        "D6: the watchdog must run before anything that can hang or abort"


def test_saturday_token_nag_runs(sb):
    (sb.root / "token.json").write_text("{}")
    sb.tick(dow="6", hm="1000")
    assert any("token-age" in c for c in sb.calls()), \
        "D5: the nag must be eligible on every tick, weekends included"


def test_nag_marker_only_on_delivered(sb):
    (sb.root / "token.json").write_text("{}")
    sb.tick(dow="6", hm="1000", env={"TICK_RC_run_notify": "1"})
    assert not (sb.logs / ".tokennag-2026-07-24").exists(), \
        "an undelivered nag must retry on the next tick"
    sb.tick(dow="6", hm="1005")
    assert (sb.logs / ".tokennag-2026-07-24").exists()


def test_eod_sync_failure_retries_sync_only(sb):
    """D10: split markers -- a GitHub hiccup must retry ONLY the push, never
    re-run the 547-name trading day."""
    sb.tick(dow="5", hm="1700", env={"TICK_RC_inline_sync": "1"})
    assert (sb.logs / ".dailyran-2026-07-24").exists()
    assert not (sb.logs / ".synced-2026-07-24").exists()
    assert any("sync-failed" in c for c in sb.calls())
    sb.tick(dow="5", hm="1705")
    assert (sb.logs / ".synced-2026-07-24").exists()
    assert sum("run_daily.py" in c for c in sb.calls()) == 1, \
        "the trading day was re-run because a push failed"


def test_intraday_sync_failure_alerts(sb):
    env = {"TICK_OUT_run_intraday": "acct: closed 1 at TP (GDX)",
           "TICK_RC_inline_sync": "1"}
    r = sb.tick(dow="5", hm="1000", env=env)
    assert any("sync-failed" in c for c in sb.calls()), \
        "D10: a booked trade whose push failed must alert"
    assert r.returncode != 0


def test_intraday_nonzero_rc_alerts_even_without_error_text(sb):
    env = {"TICK_OUT_run_intraday": "Traceback (most recent call last):",
           "TICK_RC_run_intraday": "1"}
    r = sb.tick(dow="5", hm="1000", env=env)
    assert any("intraday-errors" in c for c in sb.calls()), \
        "D1: a bare-traceback crash slipped the case-sensitive ERROR grep"
    assert r.returncode != 0


def test_heartbeat_written_every_tick(sb):
    import json as _json
    sb.tick(dow="5", hm="1000")
    hb = sb.root / "data" / "live" / "heartbeat.json"
    assert hb.exists(), "D14: no heartbeat, nothing off-VPS can judge freshness"
    rec = _json.loads(hb.read_text())
    assert rec["today"] == "2026-07-24"
    assert rec["dailyran"] is False


def test_heartbeat_reflects_completed_day(sb):
    import json as _json
    sb.tick(dow="5", hm="1700")
    rec = _json.loads((sb.root / "data" / "live" / "heartbeat.json").read_text())
    assert rec["dailyran"] is True


def test_missing_token_file_still_reaches_the_nag(sb):
    """D5b: the [ -f "$TOKEN" ] gate meant a deleted token.json never even
    invoked token-age -- silent forever. The Python side owns the
    fresh-install-vs-ran-before judgment now; the tick just always asks."""
    # NO token.json written into the sandbox
    sb.tick(dow="6", hm="1000")
    assert any("token-age" in c for c in sb.calls()), \
        "D5b: an absent token file must still be judged by run_notify"
