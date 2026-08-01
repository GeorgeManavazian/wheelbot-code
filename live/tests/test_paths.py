import importlib
import os


def test_default_state_root_is_unchanged():
    """The VPS must keep writing exactly where it always did."""
    from live.paths import state_root
    os.environ.pop("WHEELBOT_STATE_DIR", None)
    assert state_root() == "data/live"


def test_env_override(monkeypatch):
    from live.paths import state_root
    monkeypatch.setenv("WHEELBOT_STATE_DIR", "/tmp/synced")
    assert state_root() == "/tmp/synced"


def test_in_state_joins_under_root(monkeypatch):
    from live.paths import in_state
    monkeypatch.setenv("WHEELBOT_STATE_DIR", "/tmp/synced")
    assert in_state("accounts") == "/tmp/synced/accounts"
    assert in_state("accounts", "5k_N1") == "/tmp/synced/accounts/5k_N1"


def test_all_modules_follow_the_override(tmp_path):
    """accounts / gaps / config / health must all relocate together, or the
    dashboard reads a half-synced picture (fresh trades, stale gap ledger).

    E6: the old version importlib.reload()ed the modules -- something NO
    production code does -- so it proved only that a reload picks up the env,
    the OPPOSITE of the real contract. The real contract (and the A14-skeptic
    incident's lesson) is: the env is read at IMPORT time, so it must be set
    BEFORE the interpreter imports anything (systemd sets EnvironmentFile,
    then execs python). Test exactly that: a fresh interpreter."""
    import subprocess
    import sys
    code = ("import live.accounts, live.gaps, live.config, live.health\n"
            "print(live.accounts.ACCOUNTS_ROOT)\n"
            "print(live.gaps.GAPS_PATH)\n"
            "print(live.config.CONFIG_PATH)\n"
            "print(live.health.LOGS_DIR)\n")
    env = dict(os.environ, WHEELBOT_STATE_DIR=str(tmp_path), PYTHONPATH=".")
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [
        os.path.join(str(tmp_path), "accounts"),
        os.path.join(str(tmp_path), "gaps.jsonl"),
        os.path.join(str(tmp_path), "config.json"),
        os.path.join(str(tmp_path), "logs"),
    ], "a module bound its path somewhere other than the pre-import env"


def test_modules_restore_default_after_env_cleared():
    import importlib
    import live.paths, live.accounts, live.gaps, live.config, live.health
    os.environ.pop("WHEELBOT_STATE_DIR", None)
    for mod in (live.paths, live.accounts, live.gaps, live.config, live.health):
        importlib.reload(mod)
    assert live.accounts.ACCOUNTS_ROOT == "data/live/accounts"
    assert live.gaps.GAPS_PATH == "data/live/gaps.jsonl"
