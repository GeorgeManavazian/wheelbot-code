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


def test_all_modules_follow_the_override(monkeypatch):
    """accounts / gaps / config / health must all relocate together, or the
    dashboard reads a half-synced picture (fresh trades, stale gap ledger)."""
    monkeypatch.setenv("WHEELBOT_STATE_DIR", "/tmp/synced")
    import live.paths, live.accounts, live.gaps, live.config, live.health
    for mod in (live.paths, live.accounts, live.gaps, live.config, live.health):
        importlib.reload(mod)
    assert live.accounts.ACCOUNTS_ROOT == "/tmp/synced/accounts"
    assert live.gaps.GAPS_PATH == "/tmp/synced/gaps.jsonl"
    assert live.config.CONFIG_PATH == "/tmp/synced/config.json"
    assert live.health.LOGS_DIR == "/tmp/synced/logs"


def test_modules_restore_default_after_env_cleared():
    import importlib
    import live.paths, live.accounts, live.gaps, live.config, live.health
    os.environ.pop("WHEELBOT_STATE_DIR", None)
    for mod in (live.paths, live.accounts, live.gaps, live.config, live.health):
        importlib.reload(mod)
    assert live.accounts.ACCOUNTS_ROOT == "data/live/accounts"
    assert live.gaps.GAPS_PATH == "data/live/gaps.jsonl"
