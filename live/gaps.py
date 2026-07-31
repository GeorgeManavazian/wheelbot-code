"""Append-only ledger of trading days the bot failed to trade.

A missed day is NOT recoverable: Schwab exposes no historical option-chain
endpoint (`chain_frame` pulls with `from_date=today`), so what a chain looked
like on a past date cannot be retrieved and that day's entries, assignments and
expiries are gone. Reconstructing them from any other source would inject
look-ahead bias. So we record and disclose instead.

Any forward-test result read off this store must report its gap days alongside
it, the same way the STATUS notes disclose contaminated runs."""
from __future__ import annotations

from live.paths import in_state
import json
import os

GAPS_PATH = in_state("gaps.jsonl")


def recorded_dates(path: str = GAPS_PATH) -> set:
    """Every date already in the ledger. Missing file -> empty set. Corrupt
    lines are skipped, never fatal -- a half-written line must not blind the
    idempotency check and cause duplicate records forever after."""
    out = set()
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.add(json.loads(line)["date"])
                except (ValueError, KeyError, TypeError):
                    continue
    except FileNotFoundError:
        return out
    return out


def append_gap(date, reason: str, path: str = GAPS_PATH, **extra) -> bool:
    """Record one missed day. Idempotent PER DATE (not per reason): a day is
    either traded or it isn't, and the health check may run repeatedly on the
    same day. Returns False when the date was already recorded."""
    date = str(date)
    if date in recorded_dates(path):
        return False
    rec = {"date": date, "reason": reason}
    rec.update(extra)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return True


def append_correction(date, reason: str, path: str = GAPS_PATH, **extra) -> bool:
    """Re-file a day under a different reason. Unlike `append_gap` this is NOT
    idempotent-per-date: it always writes, because its whole purpose is to
    supersede a record that already exists.

    Why a second line and not an edit: the original record is evidence of what
    the bot believed at the time it failed, and this ledger is the disclosure of
    record. 2026-07-24 went in as a bare `pull_failure` -- true but misleading,
    because the bot did not skip the day, it STEPPED it against 3-6 day old
    option marks and stamped it complete. Readers of a line saying `pull_failure`
    would assume the day is simply absent from the equity curve. It isn't; it is
    in there, as a measurement of nothing. `gap_summary` folds later records over
    earlier ones per date, so the correction is what gets displayed while the
    superseded line stays on disk."""
    rec = {"date": str(date), "reason": reason, "correction": True}
    rec.update(extra)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return True


def gap_summary(path: str = GAPS_PATH) -> dict:
    """Human-facing summary of the ledger, for the dashboard.

    This ledger was write-only until 2026-07-29: the bot recorded missed days
    faithfully and NOTHING ever read them back, so every displayed return
    silently overstated its coverage. Disclosure that never reaches the reader
    is not disclosure."""
    recs = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and "date" in r:
                    recs.append(r)
    except FileNotFoundError:
        pass
    # Fold: one record per date, last line wins. Corrections are appended rather
    # than edited (see append_correction), so without this a corrected day would
    # be displayed twice -- under both the wrong reason and the right one.
    latest = {}
    for r in recs:
        latest[str(r["date"])] = r
    recs = list(latest.values())
    recs.sort(key=lambda r: str(r["date"]))
    return {
        "count": len(recs),
        "dates": [str(r["date"]) for r in recs],
        "records": recs,
        "reasons": sorted({str(r.get("reason", "?")) for r in recs}),
    }
