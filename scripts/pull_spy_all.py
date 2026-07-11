"""Bulk-pull ALL available SPY option greeks/eod chains from the local ThetaData
terminal, one file per expiration (resumable), with parallelism + retries.

Designed for a multi-hour/day run before a subscription lapses. Safe to kill and
restart — completed expirations are skipped. Each greeks/eod call is ~20-30s.

Run (detached, survives the session):
  PYTHONPATH=. nohup .venv/bin/python -m scripts.pull_spy_all \
      --out-dir data/options/all --strike-range 30 --window-days 50 \
      > data/options/pull_all.log 2>&1 &

Then concat when done:
  .venv/bin/python -m scripts.pull_spy_all --concat --out-dir data/options/all
"""
from __future__ import annotations
import argparse
import socket
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import pandas as pd
from src.engine_v2.options.theta_client import ThetaClient, ThetaError
from src.engine_v2.options.chain import normalize_greeks_eod

_print_lock = threading.Lock()


def _log(msg: str):
    with _print_lock:
        print(msg, flush=True)


def _pull_one(client, symbol, exp, window_days, strike_range):
    """Return a normalized frame, None (no data / 472), or 'TIMEOUT'.
    Retries with a shrinking window on timeout."""
    for wd in (window_days, window_days // 2, max(10, window_days // 4)):
        start = exp - pd.Timedelta(days=wd)
        try:
            raw = client.chain_greeks_eod(symbol, exp, start, exp, strike_range)
            return normalize_greeks_eod(raw) if len(raw) else None
        except ThetaError as e:
            if e.code == 472:          # no data for this expiration/tier
                return None
            _log(f"  ! {exp.date()} ThetaError {e.code}: {e}")
            return None
        except (socket.timeout, urllib.error.URLError, TimeoutError):
            continue                    # retry with a smaller window
    return "TIMEOUT"


def _worker(symbol, exp, out_dir, window_days, strike_range, counter):
    path = out_dir / f"{symbol}_{exp:%Y%m%d}.parquet"
    empty = out_dir / f"{symbol}_{exp:%Y%m%d}.empty"
    timeout_mark = out_dir / f"{symbol}_{exp:%Y%m%d}.timeout"
    if path.exists() or empty.exists():
        return "skip"
    client = ThetaClient(timeout=300)   # per-thread client (stateless, thread-safe enough)
    r = _pull_one(client, symbol, exp, window_days, strike_range)
    i, n = counter()
    if r is None:
        empty.touch()
        _log(f"[{i}/{n}] {exp.date()} no-data")
        return "empty"
    if isinstance(r, str) and r == "TIMEOUT":
        timeout_mark.touch()
        _log(f"[{i}/{n}] {exp.date()} TIMEOUT (marked for retry)")
        return "timeout"
    r.to_parquet(path)
    _log(f"[{i}/{n}] {exp.date()} OK {r.shape[0]} rows")
    return "ok"


def run(symbol, out_dir, window_days, strike_range, start_year, workers, retry_timeouts):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    client = ThetaClient(timeout=60)
    if not client.is_up():
        raise SystemExit("ThetaData terminal not running — see docs/thetadata-v3-access.md")
    today = pd.Timestamp.today().normalize()
    exps = [e for e in client.list_expirations(symbol) if e.year >= start_year and e <= today]
    exps = sorted(set(exps))
    if retry_timeouts:  # clear timeout markers so those expirations are retried
        for e in exps:
            tm = out_dir / f"{symbol}_{e:%Y%m%d}.timeout"
            if tm.exists():
                tm.unlink()
    _log(f"symbol={symbol} expirations={len(exps)} ({exps[0].date()}..{exps[-1].date()}) "
         f"workers={workers} strike_range={strike_range} window={window_days}d")

    done = {"n": 0}
    total = len(exps)

    def counter():
        with _print_lock:
            done["n"] += 1
            return done["n"], total

    stats = {"ok": 0, "empty": 0, "timeout": 0, "skip": 0}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_worker, symbol, e, out_dir, window_days, strike_range, counter)
                for e in exps]
        for f in as_completed(futs):
            stats[f.result()] += 1
    _log(f"DONE: {stats}")


def concat(symbol, out_dir):
    out_dir = Path(out_dir)
    files = sorted(out_dir.glob(f"{symbol}_*.parquet"))
    if not files:
        raise SystemExit(f"no per-expiration parquets in {out_dir}")
    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True).sort_values(
        ["date", "expiry", "strike", "right"]).reset_index(drop=True)
    dest = out_dir.parent / f"{symbol.lower()}_greeks_eod_all.parquet"
    df.to_parquet(dest)
    _log(f"concat {len(files)} files -> {dest}: {df.shape}, "
         f"dates {df.date.min().date()}..{df.date.max().date()}, expiries {df.expiry.nunique()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--out-dir", default="data/options/all")
    ap.add_argument("--window-days", type=int, default=50)
    ap.add_argument("--strike-range", type=int, default=30)
    ap.add_argument("--start-year", type=int, default=2012)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--retry-timeouts", action="store_true")
    ap.add_argument("--concat", action="store_true", help="concat per-exp files into one parquet")
    args = ap.parse_args()
    if args.concat:
        concat(args.symbol, args.out_dir)
    else:
        run(args.symbol, args.out_dir, args.window_days, args.strike_range,
            args.start_year, args.workers, args.retry_timeouts)


if __name__ == "__main__":
    main()
