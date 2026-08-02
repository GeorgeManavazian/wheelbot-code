"""A10b/C16b: N-consecutive-stepped-days escalation for conditions that log
daily but never email.

Two classes today (one mechanism):
- `unquoted_leg` (A10b): a held leg's OCC symbol returns no quote -- dead
  symbol after a reverse split / symbol change / delisting. TP suspended,
  print-only, forever.
- `call_gated_unclosable` (C16b): the A3b guard refuses the covered call
  daily; shares sit naked. Measured: SLV router backtest shows 295 gated
  days -- multi-week naked stretches are real.

Rule: a condition seen on ESCALATION_DAYS consecutive STEPPED days emails
(then keeps emailing daily while it persists, the A10 FROZEN-alert shape --
run_daily's already_stepped guard means one alert per day). A day the
condition is absent resets its counter. Keys are global across the 25
accounts by design: both classes are market/data conditions on a ticker or
contract, not account sizing -- the alternate-universes ruling is about
liquidity judgment, not alerting (declared).

Counter state persists in the synced store (escalations.json). Days the bot
did not step at all neither increment nor reset -- a VPS outage does not
launder a dead symbol's streak (declared: "consecutive" means consecutive
stepped days). --smoke never touches this file (A23 class); the caller gates.
"""
from __future__ import annotations

import json
import os
import tempfile

from live.paths import in_state

ESCALATION_DAYS = 3    # provisional owner default 2026-08-02; veto cheap
PATH = in_state("escalations.json")


def _load(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _save(state, path):
    dirn = os.path.dirname(path) or "."
    os.makedirs(dirn, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dirn, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def update_escalations(day: str, seen: dict, path: str = PATH,
                       threshold: int = ESCALATION_DAYS) -> dict:
    """Advance the counters for one stepped day and return what escalates.

    seen: {kind: iterable of keys observed today}. Pass a kind with an empty
    list when its surface RAN and found nothing -- that is what resets its
    keys. A kind absent from `seen` is left untouched entirely.

    Returns {kind: [(key, count), ...]} for every key at count >= threshold,
    deterministic order. Same-day re-calls are idempotent: a key already
    stamped `day` keeps its count, and keys stamped `day` are never reset by
    a later same-day call (a partial-failure retry must not zero a streak).
    """
    state = _load(path)
    out = {}
    for kind, keys in seen.items():
        kstate = state.setdefault(kind, {})
        today = {str(k) for k in keys}
        for key in today:
            e = kstate.get(key)
            if e is None or e["last"] != day:
                kstate[key] = {"count": (e["count"] + 1 if e else 1),
                               "last": day}
        for key in [k for k in kstate if k not in today]:
            if kstate[key]["last"] != day:      # same-day survivor keeps streak
                del kstate[key]
        rows = sorted((k, v["count"]) for k, v in kstate.items()
                      if v["count"] >= threshold)
        if rows:
            out[kind] = rows
    _save(state, path)
    return out
