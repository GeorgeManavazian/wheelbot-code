"""F7: the reset that opens day 1, and the epoch that keeps it honest.

Pre-fix: no reset tooling existed anywhere. `live/accounts.py` only DERIVES
paths; nothing created or cleared a store, so "reset the accounts" was an
undefined manual `rm` -- the way a forward test inherits half the record it
meant to discard.

The epoch half matters as much: health._scan_start falls back to a 10-day
lookback when a store has no completion marker and no gap. A brand-new store
hits that fallback, so day 1 would open by recording ~8 weekday `no_run` gaps
and emailing every one -- for days on which nothing existed to run.
"""
import datetime as _dt
import json
import os
import subprocess

import pytest

from live.health import check_day
from live.reset import (epoch_path, main, plan_reset, read_epoch, reset_store)


def _store(tmp_path):
    """A populated live store, laid out like the real one."""
    root = tmp_path / "live"
    (root / "accounts" / "100k_N1").mkdir(parents=True)
    (root / "accounts" / "_smoke").mkdir()
    (root / "logs").mkdir()
    (root / "accounts" / "100k_N1" / "state.json").write_text('{"cash": 91000}')
    (root / "accounts" / "100k_N1" / "trades.jsonl").write_text('{"t": 1}\n')
    (root / "accounts" / "100k_N1" / "snapshots.jsonl").write_text('{"s": 1}\n')
    (root / "accounts" / "100k_N1" / "state.json.bak-2026-07-20").write_text("{}")
    (root / "gaps.jsonl").write_text('{"date": "2026-07-23", "reason": "no_run"}\n')
    (root / "benchmark.jsonl").write_text('{"date": "2026-07-23"}\n')
    (root / "logs" / ".dailyran-2026-07-30").write_text("")
    return root


# --- the reset ---------------------------------------------------------------

def test_dry_run_moves_nothing(tmp_path, capsys):
    root = _store(tmp_path)
    assert main(["--root", str(root), "--stamp", "2026-08-03"]) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert (root / "gaps.jsonl").exists()
    assert (root / "accounts" / "100k_N1" / "trades.jsonl").exists()


def test_reset_refuses_without_confirm(tmp_path):
    root = _store(tmp_path)
    with pytest.raises(RuntimeError):
        reset_store(str(root), "2026-08-03")
    assert (root / "gaps.jsonl").exists()


def test_reset_archives_everything_and_opens_empty(tmp_path):
    root = _store(tmp_path)
    plan = reset_store(str(root), "2026-08-03", confirm=True)
    archive = tmp_path / "archive-2026-08-03"

    # nothing destroyed
    assert (archive / "gaps.jsonl").exists()
    assert (archive / "benchmark.jsonl").exists()
    assert (archive / "accounts" / "100k_N1" / "trades.jsonl").exists()
    assert (archive / "accounts" / "100k_N1" / "state.json.bak-2026-07-20").exists()
    assert (archive / "accounts" / "_smoke").exists()
    assert (archive / "logs" / ".dailyran-2026-07-30").exists()

    # nothing carried forward
    assert not (root / "gaps.jsonl").exists()
    assert not (root / "benchmark.jsonl").exists()
    assert os.listdir(root / "accounts") == []
    assert [n for n in os.listdir(root / "logs")
            if not n.startswith(".epoch-")] == []
    assert plan["epoch"] == "2026-08-03"


def test_reset_keeps_the_mirror_repo(tmp_path):
    """.git must survive: the store IS the mirror repo. Re-initialising would
    force-push a fresh history over the remote. The archive move instead
    stages ~200 deletions -- the exact case F4a fixed."""
    root = _store(tmp_path)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    head = (root / ".git" / "HEAD").read_text()
    reset_store(str(root), "2026-08-03", confirm=True)
    assert (root / ".git" / "HEAD").read_text() == head
    assert not (tmp_path / "archive-2026-08-03" / ".git").exists()


def test_reset_refuses_to_merge_into_an_existing_archive(tmp_path):
    root = _store(tmp_path)
    (tmp_path / "archive-2026-08-03").mkdir()
    with pytest.raises(RuntimeError, match="archive already exists"):
        reset_store(str(root), "2026-08-03", confirm=True)
    assert (root / "gaps.jsonl").exists(), "must not half-move before refusing"


def test_reset_writes_the_epoch(tmp_path):
    root = _store(tmp_path)
    reset_store(str(root), "2026-08-03", confirm=True)
    assert read_epoch(str(root / "logs")) == _dt.date(2026, 8, 3)


def test_unparseable_epoch_is_skipped_not_trusted(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / ".epoch-2026-99-99").write_text("")      # regex-shaped, invalid
    (logs / ".epoch-2026-08-03").write_text("")
    assert read_epoch(str(logs)) == _dt.date(2026, 8, 3)


def test_read_epoch_absent_is_none(tmp_path):
    (tmp_path / "logs").mkdir()
    assert read_epoch(str(tmp_path / "logs")) is None


# --- the epoch floor in the dead-man's switch --------------------------------

def _after_close(day):
    return _dt.datetime.combine(day, _dt.time(23, 50))


def test_fresh_store_records_no_historic_gaps(tmp_path):
    """THE point of the epoch. A store opened today owes exactly one run --
    today's. Without the floor, the 10-day fallback invents ~8 no_run gaps
    for days that never existed, and every one of them emails."""
    root = _store(tmp_path)
    reset_store(str(root), "2026-08-03", confirm=True)
    logs, gaps = str(root / "logs"), str(root / "gaps.jsonl")

    status, missed = check_day(_after_close(_dt.date(2026, 8, 3)),
                               logs_dir=logs, gaps_path=gaps)
    assert missed == ["2026-08-03"], missed
    assert status == "gap_recorded"


def test_epoch_does_not_hide_a_real_outage_after_it(tmp_path):
    """The floor must not become a blindfold: weekdays AFTER the epoch with no
    completion marker are still real missed days."""
    root = _store(tmp_path)
    reset_store(str(root), "2026-07-27", confirm=True)     # a Monday
    logs, gaps = str(root / "logs"), str(root / "gaps.jsonl")

    _status, missed = check_day(_after_close(_dt.date(2026, 7, 31)),
                                logs_dir=logs, gaps_path=gaps)
    assert missed == ["2026-07-27", "2026-07-28", "2026-07-29",
                      "2026-07-30", "2026-07-31"], missed


def test_epoch_floor_beats_the_ten_day_fallback(tmp_path):
    """Epoch 3 weekdays back: the fallback would have reached 10 days."""
    root = _store(tmp_path)
    reset_store(str(root), "2026-07-29", confirm=True)
    logs, gaps = str(root / "logs"), str(root / "gaps.jsonl")

    _status, missed = check_day(_after_close(_dt.date(2026, 7, 31)),
                                logs_dir=logs, gaps_path=gaps)
    assert missed == ["2026-07-29", "2026-07-30", "2026-07-31"], missed


def test_a_completed_day_after_the_epoch_is_not_a_gap(tmp_path):
    root = _store(tmp_path)
    reset_store(str(root), "2026-08-03", confirm=True)
    logs, gaps = str(root / "logs"), str(root / "gaps.jsonl")
    open(os.path.join(logs, ".dailyran-2026-08-03"), "w").close()

    status, missed = check_day(_after_close(_dt.date(2026, 8, 3)),
                               logs_dir=logs, gaps_path=gaps)
    assert (status, missed) == ("ok", [])


def test_evidence_older_than_the_epoch_cannot_drag_the_scan_back(tmp_path):
    """The floor is an invariant, not just a fresh-install convenience: the
    scan NEVER starts before the epoch, whatever evidence is lying around.

    Reachable two ways -- a back-dated `--stamp`, or an operator copying an
    archived gaps.jsonl/logs back into the live store to look something up.
    Without the floor on this branch, one stale pre-epoch marker reopens the
    whole pre-epoch window and gaps every weekday in it.
    """
    root = _store(tmp_path)
    reset_store(str(root), "2026-07-30", confirm=True)
    logs, gaps = root / "logs", root / "gaps.jsonl"
    # evidence of life from BEFORE this store existed
    (logs / ".dailyran-2026-07-22").write_text("")
    gaps.write_text('{"date": "2026-07-21", "reason": "no_run"}\n')

    _status, missed = check_day(_after_close(_dt.date(2026, 7, 31)),
                                logs_dir=str(logs), gaps_path=str(gaps))
    assert missed == ["2026-07-30", "2026-07-31"], missed


def test_no_epoch_keeps_the_old_fallback(tmp_path):
    """Stores that predate the epoch (and any non-reset store) behave exactly
    as before -- the floor is additive, not a behaviour change."""
    logs, gaps = tmp_path / "logs", tmp_path / "gaps.jsonl"
    logs.mkdir()
    _status, missed = check_day(_after_close(_dt.date(2026, 8, 3)),
                                logs_dir=str(logs), gaps_path=str(gaps))
    assert len(missed) > 5, missed
    assert missed[0] == "2026-07-24", missed


def test_gap_ledger_actually_written(tmp_path):
    root = _store(tmp_path)
    reset_store(str(root), "2026-08-03", confirm=True)
    gaps = root / "gaps.jsonl"
    check_day(_after_close(_dt.date(2026, 8, 3)),
              logs_dir=str(root / "logs"), gaps_path=str(gaps))
    recs = [json.loads(l) for l in gaps.read_text().splitlines() if l.strip()]
    assert [r["date"] for r in recs] == ["2026-08-03"], recs


# --- preserved operational config --------------------------------------------

def test_config_json_survives_the_reset(tmp_path):
    """config.json is CONFIG (n, capital, zombie_threshold, real_money), not
    track record. A reset that archived it would silently revert the bot to
    built-in defaults -- changing how it trades as a side effect of clearing
    the books."""
    root = _store(tmp_path)
    (root / "config.json").write_text('{"n": 3, "capital": 250000}')
    reset_store(str(root), "2026-08-03", confirm=True)

    assert json.loads((root / "config.json").read_text()) == {"n": 3, "capital": 250000}
    # and the archive is still a complete snapshot of the day
    assert json.loads((tmp_path / "archive-2026-08-03" / "config.json").read_text()) \
        == {"n": 3, "capital": 250000}


def test_iv_history_survives_the_reset(tmp_path):
    """The accrued IV store is a measurement of the MARKET, not of this store's
    trading. It takes 150 trading days to rebuild and cannot be re-derived --
    Schwab has no historical chain endpoint -- so archiving it would cost a
    year of ranking for a bookkeeping action. (`chains/` is deliberately not
    preserved; owner 2026-08-07.)"""
    root = _store(tmp_path)
    (root / "iv").mkdir()
    (root / "iv" / "2026-07-17.json").write_text('{"obs": "2026-07-17"}')
    (root / "chains").mkdir()
    (root / "chains" / "2026-07-17.json").write_text("{}")
    reset_store(str(root), "2026-08-03", confirm=True)

    assert (root / "iv" / "2026-07-17.json").exists(), \
        "150 trading days of unrecoverable observations were archived away"
    # and the archive is still a complete snapshot of the day
    assert (tmp_path / "archive-2026-08-03" / "iv" / "2026-07-17.json").exists()
    # chains ARE track record and still move
    assert not (root / "chains").exists()
    assert (tmp_path / "archive-2026-08-03" / "chains").exists()


def test_preserved_config_is_still_reported(tmp_path, capsys):
    root = _store(tmp_path)
    (root / "config.json").write_text("{}")
    main(["--root", str(root), "--stamp", "2026-08-03"])
    out = capsys.readouterr().out
    assert "keeping : 1" in out and "config.json" in out


def test_reset_with_only_preserved_entries_still_archives(tmp_path):
    """A store holding nothing but config.json must still produce an archive
    with the copy in it, not skip archiving because `moves` is empty."""
    root = tmp_path / "live"
    root.mkdir()
    (root / "config.json").write_text('{"n": 1}')
    reset_store(str(root), "2026-08-03", confirm=True)
    assert (tmp_path / "archive-2026-08-03" / "config.json").exists()
    assert (root / "config.json").exists()
