"""Bulk-pull hourly option OHLC bars for the wheel basket, one file per
expiration (resumable), with parallelism + retries.

Companion to pull_spy_all.py, which pulls EOD greeks. The two are NOT
interchangeable: the greeks/eod feed carries delta + bid/ask (needed to select a
strike and to fill it); this ohlc feed carries traded prices only (open/high/low/
close/volume/count/vwap, no greeks, no quotes). Hourly exists solely to time the
take-profit between daily closes.

Window: each contract's final --window-days of life, so any DTE in 0..window is
testable after the fact. Strikes track ATM day by day (ThetaData semantics).

Run (detached, survives the session):
  PYTHONPATH=. nohup .venv/bin/python -m scripts.pull_intraday_basket \
      --out-dir data/options/intraday --years 7 --strike-range 15 \
      --window-days 50 > data/options/pull_intraday.log 2>&1 &

Then concat per ticker:
  .venv/bin/python -m scripts.pull_intraday_basket --concat --out-dir data/options/intraday
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
from src.engine_v2.options.intraday import pull_option_intraday

BASKET = ["GDX", "SLV", "XOP", "XBI", "EEM", "EWZ", "TLT", "ARKK", "QQQ"]

_print_lock = threading.Lock()


def _log(msg: str):
    with _print_lock:
        print(msg, flush=True)


def _worker(symbol, exp, out_dir, window_days, strike_range, interval, counter):
    path = out_dir / f"{symbol}_{exp:%Y%m%d}_{interval}.parquet"
    empty = out_dir / f"{symbol}_{exp:%Y%m%d}_{interval}.empty"
    if path.exists() or empty.exists():
        return "skip"
    client = ThetaClient(timeout=600)
    start = exp - pd.Timedelta(days=window_days)
    try:
        df = pull_option_intraday(client, symbol, exp, start, exp,
                                  interval=interval, strike_range=strike_range)
    except (socket.timeout, urllib.error.URLError, TimeoutError):
        (out_dir / f"{symbol}_{exp:%Y%m%d}_{interval}.timeout").touch()
        i, n = counter()
        _log(f"[{i}/{n}] {symbol} {exp.date()} TIMEOUT (marked for retry)")
        return "timeout"
    except ThetaError as e:
        i, n = counter()
        _log(f"[{i}/{n}] {symbol} {exp.date()} ThetaError {e.code}: {e}")
        empty.touch()
        return "empty"
    i, n = counter()
    if df.empty:
        empty.touch()
        _log(f"[{i}/{n}] {symbol} {exp.date()} no-data")
        return "empty"
    df.to_parquet(path)
    _log(f"[{i}/{n}] {symbol} {exp.date()} OK {df.shape[0]} rows")
    return "ok"


def run(symbols, out_dir, years, window_days, strike_range, interval, workers,
        retry_timeouts):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    client = ThetaClient(timeout=60)
    if not client.is_up():
        raise SystemExit("ThetaData terminal not running — see docs/thetadata-v3-access.md")

    today = pd.Timestamp.today().normalize()
    cutoff = today - pd.Timedelta(days=365 * years)

    jobs = []
    for sym in symbols:
        exps = sorted({e for e in client.list_expirations(sym)
                       if cutoff <= e <= today})
        if retry_timeouts:
            for e in exps:
                tm = out_dir / f"{sym}_{e:%Y%m%d}_{interval}.timeout"
                if tm.exists():
                    tm.unlink()
        _log(f"{sym}: {len(exps)} expirations ({exps[0].date()}..{exps[-1].date()})")
        jobs += [(sym, e) for e in exps]

    total = len(jobs)
    _log(f"TOTAL {total} expiration-pulls | workers={workers} "
         f"strike_range={strike_range} window={window_days}d interval={interval}")

    done = {"n": 0}

    def counter():
        with _print_lock:
            done["n"] += 1
            return done["n"], total

    stats = {"ok": 0, "empty": 0, "timeout": 0, "skip": 0}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_worker, sym, e, out_dir, window_days, strike_range,
                          interval, counter)
                for sym, e in jobs]
        for f in as_completed(futs):
            stats[f.result()] += 1
    _log(f"DONE: {stats}")


def concat(symbols, out_dir, interval):
    out_dir = Path(out_dir)
    for sym in symbols:
        files = sorted(out_dir.glob(f"{sym}_*_{interval}.parquet"))
        if not files:
            _log(f"{sym}: no per-expiration parquets, skipping")
            continue
        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        df = df.sort_values(["expiry", "strike", "right", "timestamp"]).reset_index(drop=True)
        dest = out_dir.parent / f"{sym.lower()}_ohlc_{interval}_all.parquet"
        df.to_parquet(dest)
        _log(f"{sym}: {len(files)} files -> {dest}: {df.shape}, "
             f"{df.timestamp.min()}..{df.timestamp.max()}, {df.expiry.nunique()} expiries")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*", default=BASKET)
    ap.add_argument("--out-dir", default="data/options/intraday")
    ap.add_argument("--years", type=int, default=7)
    ap.add_argument("--window-days", type=int, default=50)
    ap.add_argument("--strike-range", type=int, default=15)
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--retry-timeouts", action="store_true")
    ap.add_argument("--concat", action="store_true")
    args = ap.parse_args()
    if args.concat:
        concat(args.symbols, args.out_dir, args.interval)
    else:
        run(args.symbols, args.out_dir, args.years, args.window_days,
            args.strike_range, args.interval, args.workers, args.retry_timeouts)


if __name__ == "__main__":
    main()
