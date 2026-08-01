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


def _iso_day(d) -> str:
    """C15: one canonical 'YYYY-MM-DD' per day. Bare str() let pd.Timestamp
    and the string form record the SAME day twice ('2026-07-24' vs
    '2026-07-24 00:00:00'), defeating idempotency, breaking correction
    folding, and silently degrading health's ISO parsing (_scan_start /
    unalerted_gaps skip unparseable dates). Unparseable input passes through
    untouched -- this normalizes, it never invents."""
    if hasattr(d, "date") and callable(getattr(d, "date")):
        try:
            d = d.date()          # datetime / pd.Timestamp -> date
        except TypeError:
            pass
    s = str(d)
    try:
        import datetime as _dt
        return _dt.date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return s


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
                    out.add(_iso_day(json.loads(line)["date"]))
                except (ValueError, KeyError, TypeError):
                    continue
    except FileNotFoundError:
        return out
    return out


def append_gap(date, reason: str, path: str = GAPS_PATH, **extra) -> bool:
    """Record one missed day. Idempotent PER DATE (not per reason): a day is
    either traded or it isn't, and the health check may run repeatedly on the
    same day. Returns False when the date was already recorded."""
    date = _iso_day(date)
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
    rec = {"date": _iso_day(date), "reason": reason, "correction": True}
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
    # Fold: one record per date. C14: only a `correction: True` record may
    # supersede -- the old last-line-wins let a plain later duplicate (manual
    # append, write race, date-type drift) silently replace an earlier RICHER
    # record with zero disclosure. First plain record wins; corrections
    # supersede; the last correction wins among corrections.
    latest = {}
    for r in recs:
        k = _iso_day(r["date"])
        # Stamp the normalized day back onto the record (skeptic F4): folding
        # by the normalized key while returning the raw date left a
        # timestamp-shaped no_run line permanently unalertable -- health's
        # fromisoformat skipped it -- and leaked the raw form to the display.
        r["date"] = k
        if k not in latest or r.get("correction") is True:
            latest[k] = r
    recs = list(latest.values())
    recs.sort(key=lambda r: str(r["date"]))
    return {
        "count": len(recs),
        "dates": [str(r["date"]) for r in recs],
        "records": recs,
        "reasons": sorted({str(r.get("reason", "?")) for r in recs}),
    }
