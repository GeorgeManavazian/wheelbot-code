import datetime as dt
from live.health import marker_path, check_day
from live.gaps import recorded_dates


def _et(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm)


def test_marker_path_shape(tmp_path):
    assert marker_path("2026-07-24", str(tmp_path)).endswith(".dailyran-2026-07-24")


def test_weekend_is_not_a_gap(tmp_path):
    # 2026-07-25 is a Saturday
    r, missed = check_day(_et(2026, 7, 25, 23, 50), str(tmp_path), str(tmp_path / "g.jsonl"))
    assert r == "not_weekday" and missed == []


def test_before_cutoff_does_nothing(tmp_path):
    """18:00 is inside the retry window -- the day may still succeed."""
    # 2026-07-24 is a Friday
    r, missed = check_day(_et(2026, 7, 24, 18, 0), str(tmp_path), str(tmp_path / "g.jsonl"))
    assert r == "too_early" and missed == []


def test_marker_present_means_ok(tmp_path):
    open(marker_path("2026-07-24", str(tmp_path)), "w").close()
    r, missed = check_day(_et(2026, 7, 24, 23, 50), str(tmp_path), str(tmp_path / "g.jsonl"),
                  lookback_days=0)
    assert r == "ok" and missed == []


def test_missing_marker_records_gap(tmp_path):
    g = str(tmp_path / "g.jsonl")
    r, missed = check_day(_et(2026, 7, 24, 23, 50), str(tmp_path), g, lookback_days=0)
    assert r == "gap_recorded"
    assert missed == ["2026-07-24"]
    assert recorded_dates(g) == {"2026-07-24"}


def test_running_twice_records_once(tmp_path):
    """The tick fires every 5 min after the cutoff -- one record, one alert."""
    g = str(tmp_path / "g.jsonl")
    assert check_day(_et(2026, 7, 24, 23, 50), str(tmp_path), g,
                     lookback_days=0)[0] == "gap_recorded"
    # the scan start clamps to today, so today's missed-but-recorded state
    # still reads "already_recorded" (alert delivery is owned separately by
    # run_health's unalerted-gaps scan + .gapalerted markers)
    assert check_day(_et(2026, 7, 24, 23, 50), str(tmp_path), g,
                     lookback_days=0)[0] == "already_recorded"
    with open(g) as f:
        assert len([ln for ln in f if ln.strip()]) == 1


def test_cutoff_is_after_the_eod_retry_window(tmp_path):
    """The EOD retry window runs to 23:30 ET, so the switch must not fire before
    23:45 -- otherwise it records a gap for a day a later tick could complete."""
    g = str(tmp_path / "g.jsonl")
    assert check_day(_et(2026, 7, 24, 23, 0), str(tmp_path), g,
                     lookback_days=0)[0] == "too_early"
    assert check_day(_et(2026, 7, 24, 23, 44), str(tmp_path), g,
                     lookback_days=0)[0] == "too_early"
    assert check_day(_et(2026, 7, 24, 23, 45), str(tmp_path), g,
                     lookback_days=0)[0] == "gap_recorded"


# ---- D4: forward scan from last evidence of life ----

def test_outage_beyond_lookback_fully_recorded(tmp_path):
    """VPS down 3 weeks: the fixed 10-day window recorded only the newest 10
    calendar days and silently dropped the rest -- under-disclosing exactly
    when the failure was worst. With a completion marker 2026-07-03 (Fri) and
    nothing since, EVERY weekday 07-06..07-24 must be recorded (15 days)."""
    g = str(tmp_path / "g.jsonl")
    open(marker_path("2026-07-03", str(tmp_path)), "w").close()
    r, missed = check_day(_et(2026, 7, 24, 23, 50), str(tmp_path), g)
    assert r == "gap_recorded"
    assert len(missed) == 15
    assert missed[0] == "2026-07-06" and missed[-1] == "2026-07-24"
    assert recorded_dates(g) == set(missed)


def test_fresh_install_falls_back_to_window(tmp_path):
    # no markers, no gaps anywhere: only the bounded default window applies
    g = str(tmp_path / "g.jsonl")
    r, missed = check_day(_et(2026, 7, 24, 23, 50), str(tmp_path), g,
                          lookback_days=2)
    assert r == "gap_recorded"
    assert missed == ["2026-07-22", "2026-07-23", "2026-07-24"]


def test_scan_starts_after_newest_recorded_gap(tmp_path):
    """A recorded gap is evidence the health check was alive that day -- the
    next scan must not re-walk it, only the days after it."""
    from live.gaps import append_gap
    g = str(tmp_path / "g.jsonl")
    append_gap("2026-07-22", "no_run", path=g)
    r, missed = check_day(_et(2026, 7, 24, 23, 50), str(tmp_path), g)
    assert missed == ["2026-07-23", "2026-07-24"]


def test_garbage_marker_does_not_blind_the_scan(tmp_path):
    """Group skeptic F9: a regex-shaped but unparseable marker name
    (.dailyran-2026-99-99) string-sorts above every real date; a blind
    max() then fails to parse and silently falls back to the 10-day
    window, under-recording a longer outage. The scan must key on the
    newest PARSEABLE evidence instead."""
    g = str(tmp_path / "g.jsonl")
    open(marker_path("2026-07-03", str(tmp_path)), "w").close()
    open(marker_path("2026-99-99", str(tmp_path)), "w").close()   # garbage
    r, missed = check_day(_et(2026, 7, 24, 23, 50), str(tmp_path), g)
    assert len(missed) == 15, \
        "garbage marker degraded the forward scan to the fallback window"
