import json
from live.gaps import append_gap, append_correction, gap_summary, recorded_dates


def test_append_and_read_back(tmp_path):
    p = str(tmp_path / "gaps.jsonl")
    assert recorded_dates(p) == set()
    assert append_gap("2026-07-21", "no_run", path=p) is True
    assert recorded_dates(p) == {"2026-07-21"}


def test_idempotent_per_date(tmp_path):
    """The health check reruns every 5 min after the cutoff; a day must not be
    recorded once per tick."""
    p = str(tmp_path / "gaps.jsonl")
    assert append_gap("2026-07-21", "no_run", path=p) is True
    assert append_gap("2026-07-21", "no_run", path=p) is False
    assert append_gap("2026-07-21", "pull_failure", path=p) is False
    with open(p) as f:
        assert len([ln for ln in f if ln.strip()]) == 1


def test_extra_fields_persisted(tmp_path):
    p = str(tmp_path / "gaps.jsonl")
    append_gap("2026-07-24", "pull_failure", path=p, skipped=547, universe=547)
    rec = json.loads(open(p).read().strip())
    assert rec["reason"] == "pull_failure"
    assert rec["skipped"] == 547 and rec["universe"] == 547


def test_correction_supersedes_the_earlier_record_for_that_date(tmp_path):
    """A gap can be recorded with the wrong reason (2026-07-24 was filed as a
    bare 'pull_failure' when the bot had actually stepped the day and written 25
    snapshots off stale marks). The ledger is append-only, so a correction is a
    new line that wins, not an edit."""
    p = str(tmp_path / "gaps.jsonl")
    append_gap("2026-07-24", "pull_failure", path=p, skipped=547)
    append_correction("2026-07-24", "stale_marks_recorded_as_traded", path=p,
                      snapshots_written=25)
    summary = gap_summary(p)
    assert summary["count"] == 1
    assert summary["dates"] == ["2026-07-24"]
    rec = summary["records"][0]
    assert rec["reason"] == "stale_marks_recorded_as_traded"
    assert rec["snapshots_written"] == 25
    assert summary["reasons"] == ["stale_marks_recorded_as_traded"]


def test_correction_keeps_the_superseded_line_on_disk(tmp_path):
    """The original record is evidence of what the bot believed at the time --
    a correction must never erase it."""
    p = str(tmp_path / "gaps.jsonl")
    append_gap("2026-07-24", "pull_failure", path=p)
    append_correction("2026-07-24", "stale_marks_recorded_as_traded", path=p)
    lines = [json.loads(ln) for ln in open(p) if ln.strip()]
    assert len(lines) == 2
    assert lines[0]["reason"] == "pull_failure"
    assert lines[1]["correction"] is True


def test_correction_of_an_unrecorded_date_is_just_a_record(tmp_path):
    """2026-07-27 was never in the ledger at all. Correcting it must file it."""
    p = str(tmp_path / "gaps.jsonl")
    append_correction("2026-07-27", "stale_marks_recorded_as_traded", path=p)
    assert recorded_dates(p) == {"2026-07-27"}
    assert gap_summary(p)["count"] == 1


def test_missing_file_is_empty_not_error(tmp_path):
    assert recorded_dates(str(tmp_path / "nope.jsonl")) == set()


def test_corrupt_line_skipped_not_fatal(tmp_path):
    """A truncated write must not blind the dedup check forever after."""
    p = str(tmp_path / "gaps.jsonl")
    with open(p, "w") as f:
        f.write('{"date": "2026-07-21", "reason": "no_run"}\n')
        f.write("not json at all\n")
        f.write('{"no_date_key": true}\n')
    assert recorded_dates(p) == {"2026-07-21"}
