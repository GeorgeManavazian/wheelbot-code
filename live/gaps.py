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
    recs.sort(key=lambda r: str(r["date"]))
    return {
        "count": len(recs),
        "dates": [str(r["date"]) for r in recs],
        "records": recs,
        "reasons": sorted({str(r.get("reason", "?")) for r in recs}),
    }
