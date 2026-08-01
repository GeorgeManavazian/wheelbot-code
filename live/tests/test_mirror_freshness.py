"""D14 (owner 2026-08-01: GitHub robot only): the mirror-side freshness
checker must fail loudly when the heartbeat is absent, stale, or shows an
incomplete day -- and stay quiet on weekends and healthy evenings. The
checker is standalone (no repo imports) because it runs inside the mirror's
GitHub Actions checkout, not on the VPS."""
import importlib.util
import json
import os

import pytest

_CHECKER = os.path.join(os.path.dirname(__file__), "..", "..", "deploy",
                        "check_mirror_freshness.py")


@pytest.fixture
def check():
    spec = importlib.util.spec_from_file_location("checker", _CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.check


def _hb(tmp_path, **kw):
    rec = {"tick_at": "2026-07-24T21:00:00Z", "today": "2026-07-24",
           "dailyran": True, "synced": False, "fail": False}
    rec.update(kw)
    (tmp_path / "heartbeat.json").write_text(json.dumps(rec))


# 2026-07-25 01:30 UTC == Friday 2026-07-24 21:30 ET (EDT)
FRI_CHECK = "2026-07-25T01:30:00Z"
# 2026-07-26 01:30 UTC == Saturday 2026-07-25 21:30 ET
SAT_CHECK = "2026-07-26T01:30:00Z"


def test_healthy_evening_passes(check, tmp_path):
    _hb(tmp_path)
    assert check(str(tmp_path), FRI_CHECK) == 0


def test_weekend_always_passes(check, tmp_path):
    assert check(str(tmp_path), SAT_CHECK) == 0


def test_missing_heartbeat_fails(check, tmp_path, capsys):
    assert check(str(tmp_path), FRI_CHECK) != 0
    assert "heartbeat" in capsys.readouterr().out.lower()


def test_stale_heartbeat_fails(check, tmp_path, capsys):
    _hb(tmp_path, today="2026-07-23")   # yesterday's push, VPS dead today
    assert check(str(tmp_path), FRI_CHECK) != 0
    assert "2026-07-23" in capsys.readouterr().out


def test_incomplete_day_fails(check, tmp_path, capsys):
    _hb(tmp_path, dailyran=False)
    assert check(str(tmp_path), FRI_CHECK) != 0
    assert "eod" in capsys.readouterr().out.lower()


def test_corrupt_heartbeat_fails(check, tmp_path):
    (tmp_path / "heartbeat.json").write_text("{not json")
    assert check(str(tmp_path), FRI_CHECK) != 0


# ---- the workflow + checker ride every sync (self-install) ----

def test_sync_installs_the_watcher(tmp_path, monkeypatch):
    import live.sync as sync_mod
    cfg = tmp_path / "git.json"
    cfg.write_text(json.dumps({"repo": "o/r", "token": "tok", "user": "u"}))
    state = tmp_path / "state"
    state.mkdir()
    (state / "state.json").write_text("{}")
    real = sync_mod._git

    def spy(args, cwd):
        if args[0] == "push":
            class R:
                returncode, stderr, stdout = 0, "", ""
            return R()
        return real(args, cwd)
    monkeypatch.setattr(sync_mod, "_git", spy)
    assert sync_mod.sync_state(str(state), "msg", cfg_path=str(cfg)) is True
    assert (state / ".github" / "workflows" / "mirror-freshness.yml").exists()
    assert (state / ".github" / "check_mirror_freshness.py").exists()
