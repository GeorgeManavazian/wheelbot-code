import datetime as dt
from live.health import marker_path, check_day
from live.gaps import recorded_dates


def _et(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm)


def test_marker_path_shape(tmp_path):
    assert marker_path("2026-07-24", str(tmp_path)).endswith(".dailyran-2026-07-24")


def test_weekend_is_not_a_gap(tmp_path):
    # 2026-07-25 is a Saturday
    r = check_day(_et(2026, 7, 25, 21, 0), str(tmp_path), str(tmp_path / "g.jsonl"))
    assert r == "not_weekday"


def test_before_cutoff_does_nothing(tmp_path):
    """18:00 is inside the retry window -- the day may still succeed."""
    # 2026-07-24 is a Friday
    r = check_day(_et(2026, 7, 24, 18, 0), str(tmp_path), str(tmp_path / "g.jsonl"))
    assert r == "too_early"


def test_marker_present_means_ok(tmp_path):
    open(marker_path("2026-07-24", str(tmp_path)), "w").close()
    r = check_day(_et(2026, 7, 24, 20, 30), str(tmp_path), str(tmp_path / "g.jsonl"))
    assert r == "ok"


def test_missing_marker_records_gap(tmp_path):
    g = str(tmp_path / "g.jsonl")
    r = check_day(_et(2026, 7, 24, 20, 30), str(tmp_path), g)
    assert r == "gap_recorded"
    assert recorded_dates(g) == {"2026-07-24"}


def test_running_twice_records_once(tmp_path):
    """The tick fires every 5 min after the cutoff -- one record, one alert."""
    g = str(tmp_path / "g.jsonl")
    assert check_day(_et(2026, 7, 24, 20, 30), str(tmp_path), g) == "gap_recorded"
    assert check_day(_et(2026, 7, 24, 22, 0), str(tmp_path), g) == "already_recorded"
    with open(g) as f:
        assert len([ln for ln in f if ln.strip()]) == 1


def test_cutoff_boundary_exact(tmp_path):
    """20:15 exactly is late enough; 20:14 is not."""
    g = str(tmp_path / "g.jsonl")
    assert check_day(_et(2026, 7, 24, 20, 14), str(tmp_path), g) == "too_early"
    assert check_day(_et(2026, 7, 24, 20, 15), str(tmp_path), g) == "gap_recorded"
