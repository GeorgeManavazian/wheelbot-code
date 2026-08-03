"""F4a: a staged DELETION must not be read as a credential leak.

secret_guard() resolved every staged path against the WORKTREE. A staged
deletion has no worktree file, so open() raised FileNotFoundError and the D8
"unreadable == offender" rule classified it as a credential. sync_state() then
aborted, reset the index, and returned False -- and because the file stays
deleted, the identical abort repeated on every subsequent tick forever.

This is not hypothetical: live/state.py's _day_boundary_backup prunes
state.json.bak-<date> older than BACKUP_KEEP_DAYS, and live/sync.py
deliberately keeps *.bak-* OUT of .gitignore, so the pruned file is TRACKED.
The first prune after resume permanently kills the mirror.

The same fail-closed wedge fired for any path git quote-escapes under
core.quotePath (a non-ASCII filename), which also resolves to nothing on disk.

Fixing it exposed the deeper defect the D8 rule was papering over: the guard
read worktree bytes while the commit ships INDEX bytes, so it never inspected
what was actually pushed. The guard now reads the index.
"""
import json
import os
import subprocess

import pytest

import live.sync as sync_mod
from live.sync import secret_guard, sync_state

PAT = "github_pat_11ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456"


def _repo(tmp_path):
    """A real git repo with one committed file, and a config pointing nowhere."""
    state = tmp_path / "state"
    (state / "accounts" / "100k_N1").mkdir(parents=True)
    cfg = tmp_path / "git.json"
    cfg.write_text(json.dumps({"repo": "o/r", "token": PAT, "user": "u"}))
    g = lambda *a: subprocess.run(["git"] + list(a), cwd=state,
                                  capture_output=True, text=True, check=False)
    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    return state, cfg, g


def _stub_push(monkeypatch):
    """Let every git command run for real except the push."""
    real = sync_mod._git

    def spy(args, cwd):
        if args[0] == "push":
            class R:
                returncode, stdout, stderr = 0, "", ""
            return R()
        return real(args, cwd)

    monkeypatch.setattr(sync_mod, "_git", spy)


# --- the wedge itself --------------------------------------------------------

def test_staged_deletion_does_not_abort_the_sync(tmp_path, monkeypatch, capsys):
    state, cfg, g = _repo(tmp_path)
    st = state / "accounts" / "100k_N1" / "state.json"
    bak = state / "accounts" / "100k_N1" / "state.json.bak-2026-07-20"
    st.write_text('{"cash": 100000}')
    bak.write_text('{"cash": 99000}')
    g("add", "-A")
    g("commit", "-q", "-m", "day1")

    os.remove(bak)          # == live/state.py _day_boundary_backup pruning
    _stub_push(monkeypatch)

    assert sync_state(str(state), "eod", cfg_path=str(cfg)) is True
    out = capsys.readouterr().out
    assert "ABORTED" not in out, out
    assert "treated as an offender" not in out, out


def test_the_wedge_does_not_repeat_forever(tmp_path, monkeypatch):
    """The original failure re-armed itself: the file stays deleted, so every
    later tick re-staged the same deletion and aborted identically."""
    state, cfg, g = _repo(tmp_path)
    bak = state / "accounts" / "100k_N1" / "state.json.bak-2026-07-20"
    (state / "accounts" / "100k_N1" / "state.json").write_text("{}")
    bak.write_text("{}")
    g("add", "-A")
    g("commit", "-q", "-m", "day1")
    os.remove(bak)
    _stub_push(monkeypatch)

    assert [sync_state(str(state), f"tick {i}", cfg_path=str(cfg))
            for i in range(3)] == [True, True, True]


def test_a_deletion_only_tick_is_actually_committed(tmp_path, monkeypatch):
    """Not enough to stop aborting -- the pruned backup must LEAVE the mirror.

    The isolation matters: on a first-ever sync, D13/D14 also stage .gitignore
    and the workflow files, so a commit fires whatever the gate reads. The real
    case is a later, quiet tick whose ONLY staged change is the prune. If the
    commit gate shares the guard's deletion-filtered list it sees an empty
    index, skips the commit, and the deletion is re-staged on every tick
    forever while each one force-pushes a mirror that never actually changes.
    """
    state, cfg, g = _repo(tmp_path)
    bak = state / "accounts" / "100k_N1" / "state.json.bak-2026-07-20"
    (state / "accounts" / "100k_N1" / "state.json").write_text("{}")
    bak.write_text("{}")
    _stub_push(monkeypatch)

    # tick 1: settles .gitignore + .github/ so they are no longer pending
    assert sync_state(str(state), "day1", cfg_path=str(cfg)) is True
    assert "bak-2026-07-20" in g("ls-files").stdout

    os.remove(bak)          # the prune is now the ONLY staged change
    assert sync_state(str(state), "eod", cfg_path=str(cfg)) is True

    tracked = g("ls-files").stdout
    assert "bak-2026-07-20" not in tracked, tracked
    assert not _staged_leftovers(state), "deletion re-stages every tick"


def _staged_leftovers(state):
    return subprocess.run(["git", "diff", "--cached", "--name-only"],
                          cwd=state, capture_output=True, text=True).stdout.strip()


def test_quote_escaped_path_is_not_an_offender(tmp_path, monkeypatch, capsys):
    """core.quotePath renders a non-ASCII staged path as an escaped literal
    that resolves to nothing on disk -- the same fail-closed wedge."""
    state, cfg, g = _repo(tmp_path)
    (state / "logs").mkdir()
    (state / "logs" / "naïve run.log").write_text("ordinary log line\n")
    _stub_push(monkeypatch)

    assert sync_state(str(state), "eod", cfg_path=str(cfg)) is True
    assert "ABORTED" not in capsys.readouterr().out


# --- what the guard actually inspects ----------------------------------------

def test_guard_reads_the_index_not_the_worktree(tmp_path, monkeypatch, capsys):
    """`git add -A` snapshots content into the index; the commit ships the
    INDEX. A body that held a credential at add-time and was overwritten
    before the guard read it was pushed with the secret intact."""
    state, cfg, g = _repo(tmp_path)
    log = state / "logs"
    log.mkdir()
    leak = log / "run.log"

    real = sync_mod._git
    fired = {"n": 0}

    def spy(args, cwd):
        if args[0] == "push":
            class R:
                returncode, stdout, stderr = 0, "", ""
            return R()
        r = real(args, cwd)
        # overwrite the worktree the instant the content is in the index
        if args[0] == "add" and fired["n"] == 0:
            fired["n"] = 1
            leak.write_text("clean line\n")
        return r

    leak.write_text(f"pushing https://x-access-token:{PAT}@github.com/o/r\n")
    monkeypatch.setattr(sync_mod, "_git", spy)

    assert sync_state(str(state), "eod", cfg_path=str(cfg)) is False
    assert "ABORTED" in capsys.readouterr().out


def test_a_secret_in_the_index_still_aborts(tmp_path, monkeypatch, capsys):
    """Regression: the ordinary case must keep working."""
    state, cfg, g = _repo(tmp_path)
    (state / "logs").mkdir()
    (state / "logs" / "run.log").write_text(
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\n")
    _stub_push(monkeypatch)

    assert sync_state(str(state), "eod", cfg_path=str(cfg)) is False
    assert "ABORTED" in capsys.readouterr().out


def test_unreadable_index_entry_is_still_an_offender(tmp_path, monkeypatch, capsys):
    """D8 must survive the rewrite: content that cannot be PROVEN clean aborts.
    Only a deletion -- which pushes no content at all -- is exempt."""
    state, cfg, _g = _repo(tmp_path)
    (state / "state.json").write_text('{"cash": 1.0}')

    real = sync_mod._git

    def spy(args, cwd):
        if args[0] == "push":
            class R:
                returncode, stdout, stderr = 0, "", ""
            return R()
        if args[0] == "show":            # index read fails
            class R:
                returncode, stdout, stderr = 128, "", "fatal: bad object"
            return R()
        return real(args, cwd)

    monkeypatch.setattr(sync_mod, "_git", spy)
    assert sync_state(str(state), "eod", cfg_path=str(cfg)) is False
    assert "ABORTED" in capsys.readouterr().out


def test_oversize_index_entry_is_an_offender(tmp_path, monkeypatch, capsys):
    state, cfg, _g = _repo(tmp_path)
    (state / "big.log").write_text("x" * (sync_mod._SCAN_MAX_BYTES + 100))
    _stub_push(monkeypatch)

    assert sync_state(str(state), "eod", cfg_path=str(cfg)) is False
    assert "ABORTED" in capsys.readouterr().out


def test_binary_content_does_not_crash_the_sync(tmp_path, monkeypatch, capsys):
    """A parquet/undecodable body must not raise out of the read path -- an
    uncaught decode error would abort every tick, which is the wedge again."""
    state, cfg, _g = _repo(tmp_path)
    (state / "blob.bin").write_bytes(bytes(range(256)) * 8)
    _stub_push(monkeypatch)

    assert sync_state(str(state), "eod", cfg_path=str(cfg)) is True
    assert "FAILED" not in capsys.readouterr().out


# --- the direct-call API is unchanged ----------------------------------------

def test_secret_guard_worktree_api_is_unchanged(tmp_path):
    """Called without a reader, the guard still scans the worktree -- the shape
    every existing test and any ad-hoc caller relies on."""
    (tmp_path / "clean.json").write_text('{"cash": 1}')
    (tmp_path / "dirty.log").write_text(f"tok {PAT}\n")
    assert secret_guard(["clean.json"], state_dir=str(tmp_path)) == []
    assert secret_guard(["dirty.log"], state_dir=str(tmp_path)) == ["dirty.log"]
    assert secret_guard(["gone.json"], state_dir=str(tmp_path)) == ["gone.json"]


def test_commit_works_in_a_repo_the_bot_did_not_create(tmp_path, monkeypatch, capsys):
    """Identity used to be set only on the init branch, so a mirror repo made
    any other way (by hand -- which is how the VPS's own store was made --
    restored, or cloned) had none. On a box with no global gitconfig the
    commit then fails with "Author identity unknown" and the sync returns
    False on every tick, permanently, with a message nothing reads.

    Reproduced by clearing HOME so no global identity can be found; without
    the per-sync config this fails on ANY machine, not just a bare VPS.
    """
    state, cfg, g = _repo(tmp_path)          # repo pre-created, no identity set
    g("config", "--unset", "user.email")
    g("config", "--unset", "user.name")
    (state / "state.json").write_text('{"cash": 1.0}')
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    _stub_push(monkeypatch)

    assert sync_state(str(state), "eod", cfg_path=str(cfg)) is True
    assert "commit failed" not in capsys.readouterr().out
    assert g("log", "-1", "--format=%an").stdout.strip() == "wheelbot"
