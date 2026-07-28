import json
import subprocess
from live.sync import secret_guard, sync_state


def test_secret_guard_flags_token_paths():
    bad = secret_guard([
        "accounts/5k_N1/state.json",
        ".schwab/token.json",
        "gaps.jsonl",
    ])
    assert bad == [".schwab/token.json"]


def test_secret_guard_flags_wheelbot_dir_and_keys():
    bad = secret_guard([
        ".wheelbot/alerts.json",
        ".wheelbot/git.json",
        "oci_api_key.pem",
        "id_rsa",
        "trades.jsonl",
    ])
    assert set(bad) == {".wheelbot/alerts.json", ".wheelbot/git.json",
                        "oci_api_key.pem", "id_rsa"}


def test_secret_guard_clean_list_is_empty():
    assert secret_guard(["state.json", "trades.jsonl", "snapshots.jsonl",
                         "gaps.jsonl", "logs/2026-07-28.log"]) == []


def test_sync_state_missing_config_returns_false(tmp_path):
    assert sync_state(str(tmp_path), "msg",
                      cfg_path=str(tmp_path / "absent.json")) is False


def test_sync_state_aborts_when_secret_staged(tmp_path, monkeypatch):
    """The guard must stop a push, not merely warn about it."""
    cfg = tmp_path / "git.json"
    cfg.write_text(json.dumps({"repo": "o/r", "token": "t", "user": "u"}))
    state = tmp_path / "state"
    state.mkdir()
    (state / "harmless.json").write_text("{}")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=state, check=True)
    monkeypatch.setattr("live.sync._staged_paths", lambda d: [".schwab/token.json"])
    assert sync_state(str(state), "msg", cfg_path=str(cfg)) is False


def test_sync_state_no_changes_still_pushes(tmp_path, monkeypatch):
    """An unchanged state dir is a normal quiet tick -- but it must STILL push.

    A commit whose push failed on an earlier tick leaves the index empty; if
    sync returned early here, the mirror would silently never catch up while
    reporting success every time."""
    import live.sync as sync_mod
    cfg = tmp_path / "git.json"
    cfg.write_text(json.dumps({"repo": "o/r", "token": "tok", "user": "u"}))
    state = tmp_path / "state"
    state.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=state, check=True)

    seen = []
    real = sync_mod._git

    def spy(args, cwd):
        seen.append(args[0])
        if args[0] == "push":
            class R:
                returncode = 0
                stderr = ""
                stdout = ""
            return R()
        return real(args, cwd)

    monkeypatch.setattr(sync_mod, "_git", spy)
    monkeypatch.setattr(sync_mod, "_staged_paths", lambda d: [])
    assert sync_state(str(state), "msg", cfg_path=str(cfg)) is True
    assert "push" in seen
    assert "commit" not in seen        # nothing staged -> no empty commit
