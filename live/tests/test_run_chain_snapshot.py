import datetime as dt
from zoneinfo import ZoneInfo

from live.run_chain_snapshot import snapshot_window_open, save_still_rth

ET = ZoneInfo("America/New_York")


def _at(day, h, m):
    return dt.datetime(2026, 7, day, h, m, tzinfo=ET)


def test_window_open_inside_rth():
    assert snapshot_window_open(_at(17, 15, 20))      # Friday, open edge
    assert snapshot_window_open(_at(17, 15, 45))
    assert snapshot_window_open(_at(17, 15, 50))      # close edge


def test_window_shut_outside():
    assert not snapshot_window_open(_at(17, 15, 19))
    # 15:51: a start here plus the measured ~7-min pull would finish past the
    # 16:00 close (skeptic F4) -- shut.
    assert not snapshot_window_open(_at(17, 15, 51))
    assert not snapshot_window_open(_at(17, 9, 30))


def test_save_recheck_refuses_post_close_finish():
    """Skeptic F4: the start gate alone let a slow pull bless post-close
    quotes as RTH; the save must re-check the clock, with a small grace for a
    pull that read the closing book seconds late."""
    assert save_still_rth(_at(17, 15, 59))
    assert save_still_rth(_at(17, 16, 5))     # grace edge
    assert not save_still_rth(_at(17, 16, 6))
    assert not save_still_rth(_at(17, 17, 0))


def test_seventeen_hundred_is_the_defect_not_the_window():
    """A16 regression pin: 17:00 ET is after the options close; a snapshot
    then is exactly the ghost book this fix removes."""
    assert not snapshot_window_open(_at(17, 17, 0))


def test_weekend_shut():
    assert not snapshot_window_open(_at(18, 15, 45))  # Saturday
    assert not snapshot_window_open(_at(19, 15, 45))  # Sunday
