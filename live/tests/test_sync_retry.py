"""Regression: a commit whose push failed must still be pushed by a later sync."""
import json, subprocess
from live.sync import sync_state


def test_push_attempted_even_when_nothing_new_to_commit(tmp_path, monkeypatch):
    cfg = tmp_path / "git.json"
    cfg.write_text(json.dumps({"repo": "o/r", "token": "t", "user": "u"}))
    state = tmp_path / "state"; state.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=state, check=True)

    calls = []
    real = __import__("live.sync", fromlist=["_git"])._git

    def spy(args, cwd):
        calls.append(args[0])
        if args[0] == "push":
            class R: returncode = 0; stderr = ""; stdout = ""
            return R()
        return real(args, cwd)

    monkeypatch.setattr("live.sync._git", spy)
    monkeypatch.setattr("live.sync._staged_paths", lambda d: [])
    assert sync_state(str(state), "msg", cfg_path=str(cfg)) is True
    assert "push" in calls, "push must be attempted even with an empty index"
