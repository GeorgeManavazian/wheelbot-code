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
