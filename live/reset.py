"""F7: take the live store back to day 1.

There was no reset tooling. "Reset the accounts" was an undefined manual `rm`,
which is how a forward test quietly inherits half of the record it meant to
discard -- a stale gaps.jsonl, a surviving .bak-*, one account's trades.jsonl
that the glob missed.

What this does:

1. MOVES the entire live store to `<root>/../archive-<stamp>/`. Nothing is
   deleted. The 2026-07-31 audit's conclusion was that the old track record
   cannot be repaired, only discarded -- discarded is not the same as
   destroyed, and it is the evidence base for every finding in the repair
   plan.
2. Leaves `.git` in place. The store is the mirror repo; a re-init would
   force-push a fresh history and orphan the remote. The archive move stages
   ~200 deletions instead, which is exactly the case F4a fixed -- before that
   fix this reset would have wedged the sync permanently on its first tick.
3. Writes `logs/.epoch-<date>`: the day this store began. The dead-man's
   switch falls back to a 10-day lookback when a store has no markers and no
   gaps, so a brand-new store would report ~8 weekday `no_run` gaps and email
   every one of them -- for days on which no bot existed. The epoch is the
   honest floor: nothing before it was ever missed, because there was
   nothing to miss.

Accounts self-initialise: `run_daily` does `load_state(path) or
PortfolioState(cash=capital)`, so an absent state.json IS a fresh account at
its full starting capital. This tool therefore creates no account files.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import shutil

from live.paths import state_root

EPOCH_RE = re.compile(r"\.epoch-(\d{4}-\d{2}-\d{2})$")


def epoch_path(logs_dir: str, day: str) -> str:
    return os.path.join(logs_dir, f".epoch-{day}")


def read_epoch(logs_dir: str):
    """The day this store began, or None. Newest parseable wins; an unparseable
    name is skipped rather than trusted (health._scan_start precedent -- a
    regex-shaped `.epoch-2026-99-99` must not become the floor)."""
    best = None
    try:
        names = os.listdir(logs_dir)
    except OSError:
        return None
    for name in names:
        m = EPOCH_RE.fullmatch(name)
        if not m:
            continue
        try:
            d = _dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if best is None or d > best:
            best = d
    return best


def plan_reset(root: str = None, stamp: str = None) -> dict:
    """What a reset would move, without moving it."""
    root = root or state_root()
    stamp = stamp or _dt.date.today().isoformat()
    parent = os.path.dirname(os.path.abspath(root)) or "."
    archive = os.path.join(parent, f"archive-{stamp}")
    try:
        entries = sorted(e for e in os.listdir(root) if e != ".git")
    except OSError:
        entries = []
    return {"root": os.path.abspath(root), "archive": archive,
            "moves": entries, "epoch": stamp}


def reset_store(root: str = None, stamp: str = None, confirm: bool = False) -> dict:
    """Archive the live store and open a fresh one. Returns the plan actually
    executed. Raises without `confirm` -- this is not a function to call by
    accident."""
    if not confirm:
        raise RuntimeError("reset_store requires confirm=True")
    plan = plan_reset(root, stamp)
    root, archive = plan["root"], plan["archive"]
    if os.path.exists(archive):
        raise RuntimeError(f"archive already exists, refusing to merge: {archive}")

    if plan["moves"]:
        os.makedirs(archive, exist_ok=False)
        for name in plan["moves"]:
            shutil.move(os.path.join(root, name), os.path.join(archive, name))

    logs = os.path.join(root, "logs")
    os.makedirs(os.path.join(root, "accounts"), exist_ok=True)
    os.makedirs(logs, exist_ok=True)
    with open(epoch_path(logs, plan["epoch"]), "w") as f:
        f.write(f"store opened {plan['epoch']}\n")
    return plan


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Reset the live store to day 1.")
    ap.add_argument("--confirm", action="store_true",
                    help="actually do it (default is a dry run)")
    ap.add_argument("--root", default=None, help="store root (default: live store)")
    ap.add_argument("--stamp", default=None, help="archive/epoch date (default: today)")
    a = ap.parse_args(argv)

    plan = plan_reset(a.root, a.stamp)
    print(f"store   : {plan['root']}")
    print(f"archive : {plan['archive']}")
    print(f"epoch   : {plan['epoch']}")
    print(f"moving  : {len(plan['moves'])} entries")
    for name in plan["moves"]:
        print(f"          {name}")
    if not a.confirm:
        print("\nDRY RUN -- nothing moved. Re-run with --confirm.")
        return 0
    reset_store(a.root, a.stamp, confirm=True)
    print("\nDONE. Fresh store: accounts/ logs/ and the epoch marker. "
          ".git kept (the mirror keeps its history; the deletions ride the "
          "next sync).")
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
