"""C13 + C14 + C15: the gaps ledger must not crash, lie, or double-count.

C13: run_daily's partial-failure path called
`append_gap(f"{day}#accounts", ..., date=day)` -- a TypeError (the first
positional IS `date`), so the per-account hole was undisclosed AND the
exception propagated after 22+ accounts stepped; post-D9 the tick would
retry-loop all evening and the dead-man would file a false no_run gap.
C14: gap_summary folded last-line-wins over ALL records, so a later plain
duplicate silently superseded an earlier richer record; only
`correction: True` may supersede.
C15: `str(date)` let pd.Timestamp and "YYYY-MM-DD" record the same day
twice, defeating idempotency, breaking correction folding, and silently
degrading health's _scan_start/unalerted_gaps ISO parsing."""
import json

import pandas as pd

from live.gaps import append_gap, append_correction, gap_summary, recorded_dates


# ---- C14 ----

def test_plain_duplicate_never_supersedes_richer_record(tmp_path):
    g = str(tmp_path / "g.jsonl")
    with open(g, "w") as f:
        f.write(json.dumps({"date": "2026-07-24", "reason": "pull_failure",
                            "skipped_closes": 547, "universe": 547}) + "\n")
        f.write(json.dumps({"date": "2026-07-24", "reason": "no_run"}) + "\n")
    rec = gap_summary(g)["records"][0]
    assert rec["reason"] == "pull_failure", \
        "C14: a plain later line silently superseded the richer record"
    assert rec["skipped_closes"] == 547


def test_correction_still_supersedes(tmp_path):
    g = str(tmp_path / "g.jsonl")
    append_gap("2026-07-24", "pull_failure", path=g)
    append_correction("2026-07-24", "stepped_on_stale_marks", path=g)
    assert gap_summary(g)["records"][0]["reason"] == "stepped_on_stale_marks"


def test_two_corrections_last_wins(tmp_path):
    g = str(tmp_path / "g.jsonl")
    append_gap("2026-07-24", "pull_failure", path=g)
    append_correction("2026-07-24", "first", path=g)
    append_correction("2026-07-24", "second", path=g)
    assert gap_summary(g)["records"][0]["reason"] == "second"


# ---- C13 (ledger side: the production call shape must work) ----

def test_partial_failure_shape_records_without_collision(tmp_path):
    g = str(tmp_path / "g.jsonl")
    append_gap("2026-07-24", "accounts_failed", path=g, accounts=["100k_N5"])
    rec = gap_summary(g)["records"][0]
    assert rec["reason"] == "accounts_failed" and rec["accounts"] == ["100k_N5"]


def test_partial_failure_after_prior_record_files_a_correction(tmp_path):
    # 17:05 zombie attempt wrote pull_failure; 17:35 retry partially stepped
    g = str(tmp_path / "g.jsonl")
    append_gap("2026-07-24", "pull_failure", path=g)
    if not append_gap("2026-07-24", "accounts_failed", path=g,
                      accounts=["100k_N5"]):
        append_correction("2026-07-24", "accounts_failed", path=g,
                          accounts=["100k_N5"])
    assert gap_summary(g)["records"][0]["reason"] == "accounts_failed"


# ---- C15 ----

def test_timestamp_and_string_are_one_day(tmp_path):
    g = str(tmp_path / "g.jsonl")
    assert append_gap(pd.Timestamp("2026-07-24"), "no_run", path=g) is True
    assert append_gap("2026-07-24", "no_run", path=g) is False, \
        "C15: idempotency defeated by date-type drift"
    assert recorded_dates(g) == {"2026-07-24"}


def test_correction_folds_across_date_types(tmp_path):
    g = str(tmp_path / "g.jsonl")
    append_gap("2026-07-24", "pull_failure", path=g)
    append_correction(pd.Timestamp("2026-07-24"), "corrected", path=g)
    recs = gap_summary(g)["records"]
    assert len(recs) == 1 and recs[0]["reason"] == "corrected"


def test_datetime_variants_normalize(tmp_path):
    import datetime as dt
    g = str(tmp_path / "g.jsonl")
    append_gap(dt.date(2026, 7, 24), "no_run", path=g)
    append_gap("2026-07-24T00:00:00", "no_run", path=g)
    assert recorded_dates(g) == {"2026-07-24"}


def test_run_daily_partial_failure_call_site_shape():
    """C13 wiring lint (the PARTIAL branch has no main()-harness test -- the
    WHEELBOT_STATE_DIR import trap makes one expensive; this pins the source
    instead). The defect WAS the call site: `append_gap(f"{day}#accounts",
    ..., date=day)` -- a TypeError, undisclosed hole + evening retry loop.
    The fixed shape keys the REAL day and falls back to a correction."""
    import inspect, re
    import live.run_daily as rd
    src = inspect.getsource(rd)
    assert "date=day" not in src, \
        "C13 regressed: append_gap called with a colliding date= kwarg"
    assert "#accounts" not in src, \
        "C13 regressed: gap keyed on a fake '<day>#accounts' date"
    # A23 renamed the call sites to the smoke-aware wrappers (_gap/_correction
    # alias the real functions off-smoke; the aliasing is behaviorally pinned
    # by test_smoke_pull_failure_touches_no_real_ledger_and_sends_no_mail).
    # The property pinned HERE is unchanged: keyed on the real day, correction
    # fallback when the day already has a record.
    m = re.search(r"if not _gap\(day, \"accounts_failed\"[\s\S]{0,120}"
                  r"_correction\(day, \"accounts_failed\"", src)
    assert m, "C13: gap-then-correction fallback shape missing from run_daily"


def test_folded_records_carry_the_normalized_date(tmp_path):
    """Skeptic F4: folding by the normalized key but returning the RAW date
    left a timestamp-shaped no_run line permanently unalertable (health's
    fromisoformat skipped it) and leaked '2026-07-24 00:00:00' to displays."""
    import json
    g = str(tmp_path / "g.jsonl")
    with open(g, "w") as f:
        f.write(json.dumps({"date": "2026-07-30 00:00:00",
                            "reason": "no_run"}) + "\n")
    summ = gap_summary(g)
    assert summ["records"][0]["date"] == "2026-07-30"
    assert summ["dates"] == ["2026-07-30"]


def test_timestamped_no_run_is_still_alertable(tmp_path, monkeypatch):
    """The dollars-adjacent consequence of F4: unalerted_gaps must see a
    drift-shaped no_run record, or a missed day recorded pre-normalization
    never re-alerts after a failed send."""
    import json
    from live.health import unalerted_gaps
    import datetime as dt
    g = str(tmp_path / "g.jsonl")
    logs = tmp_path / "logs"
    logs.mkdir()
    with open(g, "w") as f:
        f.write(json.dumps({"date": "2026-07-30 00:00:00",
                            "reason": "no_run"}) + "\n")
    out = unalerted_gaps(g, str(logs), dt.date(2026, 8, 1))
    assert out == ["2026-07-30"], \
        "F4: a timestamp-shaped no_run gap is invisible to the re-alerter"
