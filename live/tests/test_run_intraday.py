import datetime as dt
from zoneinfo import ZoneInfo
from live.run_intraday import market_is_open

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm, tzinfo=ET)


def test_open_midday_weekday():
    assert market_is_open(_et(2026, 7, 22, 12, 0)) is True     # Wed noon


def test_boundaries_inclusive():
    assert market_is_open(_et(2026, 7, 22, 9, 30)) is True     # open bell
    assert market_is_open(_et(2026, 7, 22, 16, 0)) is True     # close


def test_closed_before_and_after():
    assert market_is_open(_et(2026, 7, 22, 9, 0)) is False
    assert market_is_open(_et(2026, 7, 22, 16, 30)) is False


def test_closed_weekend():
    assert market_is_open(_et(2026, 7, 25, 12, 0)) is False     # Sat
    assert market_is_open(_et(2026, 7, 26, 12, 0)) is False     # Sun


# ---- D1: the exit code is the dead-man's contract ----

def _wire(monkeypatch, accounts=((100_000.0, 1),)):
    import types
    import sys as _sys
    import live.run_intraday as ri
    monkeypatch.setattr(ri, "market_is_open", lambda now: True)
    fake = types.ModuleType("schwab_client")
    fake.get_client = lambda: object()
    monkeypatch.setitem(_sys.modules, "schwab_client", fake)
    monkeypatch.setattr(ri, "all_accounts", lambda: list(accounts))
    return ri


def test_account_error_makes_exit_nonzero(monkeypatch):
    """A crashed account handler used to print 'ERROR' and the process still
    exited 0 -- the tick's grep was the only (case-sensitive) tripwire. The
    exit code itself must say it."""
    ri = _wire(monkeypatch)

    def boom(cap, n):
        raise RuntimeError("boom")
    monkeypatch.setattr(ri, "account_paths", boom)
    rc = ri.main()
    assert rc is not None and rc != 0


def test_wholesale_failure_makes_exit_nonzero(monkeypatch):
    """get_client dying (token lapse, import rot) used to print a bare
    traceback with no 'ERROR' literal: invisible to the old grep AND exit 0."""
    import types
    import sys as _sys
    import live.run_intraday as ri
    monkeypatch.setattr(ri, "market_is_open", lambda now: True)
    fake = types.ModuleType("schwab_client")

    def dead():
        raise RuntimeError("token lapsed")
    fake.get_client = dead
    monkeypatch.setitem(_sys.modules, "schwab_client", fake)
    rc = ri.main()
    assert rc is not None and rc != 0


def test_clean_noop_run_exits_zero(monkeypatch):
    ri = _wire(monkeypatch)
    monkeypatch.setattr(ri, "account_paths",
                        lambda cap, n: {"state": "/nonexistent/state.json"})
    monkeypatch.setattr(ri, "load_state", lambda p: None)
    assert ri.main() == 0


def test_market_closed_exits_zero(monkeypatch):
    import live.run_intraday as ri
    monkeypatch.setattr(ri, "market_is_open", lambda now: False)
    assert ri.main() == 0
