"""Concurrent full-universe pull via the thetadata PYTHON LIBRARY (no terminal).

Runs the 547-name live universe (live/universe.py) through scripts.pull_lib with
a thread pool -- the cloud allows parallel requests (the old terminal capped at 4),
so N workers give ~N x throughput. Each worker owns one ticker end-to-end
(pull all expirations -> concat), with a fresh client (thread-safety) and
per-expiration retry on transient gRPC blips. Fully resumable: a ticker whose
concat parquet already exists is skipped instantly, so restarts are cheap and the
114 already pulled via the terminal are not re-fetched.

  PYTHONPATH=. .venv-live/bin/python scripts/pull_universe_lib.py --workers 6
"""
from __future__ import annotations
import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from live.universe import UNIVERSE
from src.engine_v2.options.data import chain_path
from scripts.pull_lib import pull, concat, _client


def _do(symbol, out_dir, start_year, strike_range, window_days):
    dest = chain_path(symbol, out_dir)
    if os.path.exists(dest):
        return symbol, "skip (already concatted)"
    try:
        c = _client()                       # per-thread client
        pull(symbol, out_dir, start_year, strike_range, window_days, c=c)
        concat(symbol, out_dir)
        return symbol, "DONE" if os.path.exists(dest) else "no-data"
    except Exception as e:
        return symbol, f"ERR {str(e)[:100]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out-dir", default="data/options")
    ap.add_argument("--start-year", type=int, default=2024)
    ap.add_argument("--strike-range", type=int, default=15)
    ap.add_argument("--window-days", type=int, default=25)
    ap.add_argument("--limit", type=int, default=0, help="cap tickers (0=all)")
    args = ap.parse_args()

    todo = [t for t in UNIVERSE if not os.path.exists(chain_path(t, args.out_dir))]
    if args.limit:
        todo = todo[:args.limit]
    done_already = len(UNIVERSE) - len(todo)
    print(f"universe {len(UNIVERSE)} | already have {done_already} | to pull {len(todo)} "
          f"| workers {args.workers}", flush=True)

    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(_do, s, args.out_dir, args.start_year,
                          args.strike_range, args.window_days) for s in todo]
        for f in as_completed(futs):
            sym, res = f.result()
            done += 1
            have = len(UNIVERSE) - sum(1 for t in UNIVERSE
                                       if not os.path.exists(chain_path(t, args.out_dir)))
            print(f"[{done}/{len(todo)}] {sym}: {res}  | universe complete {have}/{len(UNIVERSE)} "
                  f"| {(time.time()-t0)/60:.0f}min", flush=True)
    print(f"=== UNIVERSE PULL COMPLETE: {have}/{len(UNIVERSE)} in {(time.time()-t0)/60:.0f}min", flush=True)


if __name__ == "__main__":
    main()
