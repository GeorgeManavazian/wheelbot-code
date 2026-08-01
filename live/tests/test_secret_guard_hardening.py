"""D8 + D13: harden the pre-push credential scan and stop syncing orphans.

Pre-fix defects (audit 2026-07-31, re-verified 2026-08-01): 9 of 12 realistic
credential shapes passed the guard; files >2MB were skipped silently;
unreadable files passed silently; the guard never checked the LITERAL tokens
it holds in config; and `git add -A` staged orphaned *.tmp files forever."""
import json
import os
import subprocess
import time

from live.sync import secret_guard, sync_state


def _write(state, name, body):
    p = state / name
    p.write_text(body)
    return name


def test_missed_shapes_are_now_flagged(tmp_path):
    state = tmp_path
    cases = {
        "a.json": '"access_token": "abcdef0123456789abcdef"',
        "b.json": '"app_key": "ABCDEF123456"',
        "c.log": "Authorization: Bearer abcdefghijklmnopqrstuv.1234567890",
        "d.txt": "-----BEGIN ENCRYPTED PRIVATE KEY-----",
        "e.txt": "aws AKIAIOSFODNN7EXAMPLE key",
    }
    for name, body in cases.items():
        _write(state, name, body)
    bad = secret_guard(list(cases), state_dir=str(state))
    assert sorted(bad) == sorted(cases), f"missed: {set(cases) - set(bad)}"


def test_linewrapped_pat_is_flagged(tmp_path):
    body = "github_pat_ABCDEFGHIJ\nKLMNOPQRSTUVWXYZ0123456789"
    _write(tmp_path, "wrapped.log", body)
    assert secret_guard(["wrapped.log"], state_dir=str(tmp_path)) == ["wrapped.log"]


def test_oversize_file_is_an_offender_not_a_skip(tmp_path):
    p = tmp_path / "big.log"
    with open(p, "w") as f:
        f.write("x" * 2_100_000)
    assert secret_guard(["big.log"], state_dir=str(tmp_path)) == ["big.log"]


def test_unreadable_file_is_an_offender_not_a_skip(tmp_path):
    p = tmp_path / "locked.json"
    p.write_text("{}")
    os.chmod(p, 0)
    try:
        assert secret_guard(["locked.json"], state_dir=str(tmp_path)) == ["locked.json"]
    finally:
        os.chmod(p, 0o644)


def test_literal_token_is_flagged(tmp_path):
    _write(tmp_path, "sneaky.log", "pushed with tok_LiteralSecretValue done")
    bad = secret_guard(["sneaky.log"], state_dir=str(tmp_path),
                       literals=("tok_LiteralSecretValue",))
    assert bad == ["sneaky.log"]


def test_clean_files_still_pass(tmp_path):
    _write(tmp_path, "state.json", '{"cash": 5000.0}')
    assert secret_guard(["state.json"], state_dir=str(tmp_path),
                        literals=("tok_x12345",)) == []


# ---- D13: .gitignore + orphaned tmp cleanup ----

def _stub_push(monkeypatch):
    import live.sync as sync_mod
    real = sync_mod._git

    def spy(args, cwd):
        if args[0] == "push":
            class R:
                returncode, stderr, stdout = 0, "", ""
            return R()
        return real(args, cwd)
    monkeypatch.setattr(sync_mod, "_git", spy)


def test_sync_writes_gitignore_and_prunes_old_tmp(tmp_path, monkeypatch):
    cfg = tmp_path / "git.json"
    cfg.write_text(json.dumps({"repo": "o/r", "token": "tok", "user": "u"}))
    state = tmp_path / "state"
    state.mkdir()
    (state / "state.json").write_text("{}")
    old = state / "orphan123.tmp"
    old.write_text("half-written")
    os.utime(old, (time.time() - 2 * 86400,) * 2)
    fresh = state / "inflight456.tmp"
    fresh.write_text("in flight")
    _stub_push(monkeypatch)
    assert sync_state(str(state), "msg", cfg_path=str(cfg)) is True
    gi = state / ".gitignore"
    assert gi.exists() and "*.tmp" in gi.read_text()
    assert not old.exists(), "day-old orphan must be deleted"
    assert fresh.exists(), "an in-flight writer's tmp must survive"
    tracked = subprocess.run(["git", "ls-files"], cwd=state,
                             capture_output=True, text=True).stdout
    assert ".tmp" not in tracked
