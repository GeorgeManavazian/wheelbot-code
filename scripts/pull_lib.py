"""Pull EOD option greeks via the thetadata PYTHON LIBRARY (no ThetaTerminal).

The library connects directly to Theta Data's cloud (auth = THETADATA_API_KEY),
so this has NO dependence on the local Java terminal that dies on Mac sleep, and
is ~2.5x faster per call. Returns the same raw column schema the engine's
normalize_greeks_eod already consumes, so we reuse it verbatim.

Per ticker: list expirations >= start-year, pull greeks_eod over the full window
for each (near-ATM band), normalize, concat to data/options/<tkr>_greeks_eod_all.parquet.
Resumable: a ticker whose concat parquet exists is skipped; per-expiration frames
are cached under data/options/lib/<TKR>/ so a restart resumes mid-ticker.

  PYTHONPATH=. .venv-live/bin/python scripts/pull_lib.py --symbol KO
  PYTHONPATH=. .venv-live/bin/python scripts/pull_lib.py --symbol KO --concat
"""
from __future__ import annotations
import argparse
import datetime as dt
import os
from pathlib import Path

import pandas as pd

from src.engine_v2.options.chain import normalize_greeks_eod
from src.engine_v2.options.data import chain_path

_API_KEY = None


def _api_key():
    global _API_KEY
    if _API_KEY is None:
        env = Path.home() / "ThetaTerminal" / ".env"
        _API_KEY = env.read_text().split("THETADATA_API_KEY=")[1].split()[0].strip().strip('"')
    return _API_KEY


def _client():
    from thetadata import ThetaClient
    return ThetaClient(api_key=_api_key(), dataframe_type="pandas")


def _greeks_with_retry(c, symbol, exp, start, strike_range, tries=4):
    """Transient gRPC blips (RESOURCE_EXHAUSTED / RPC terminated) happen under
    concurrency -- retry with backoff. Re-raise a real 'No data' as-is (caught upstream)."""
    import time
    last = None
    for a in range(tries):
        try:
            return c.option_history_greeks_eod(symbol=symbol, expiration=exp,
                                               start_date=start, end_date=exp,
                                               strike_range=strike_range)
        except Exception as ex:
            last = ex
            s = str(ex)
            if "No data found" in s:
                raise
            time.sleep(2 * (a + 1))
    raise last


def _exp_dir(symbol, out_dir):
    d = Path(out_dir) / "lib" / symbol.upper()
    d.mkdir(parents=True, exist_ok=True)
    return d


def pull(symbol, out_dir, start_year, strike_range, window_days, c=None):
    c = c or _client()          # per-worker client for thread-safety
    symbol = symbol.upper()
    today = dt.date.today()
    exps = c.option_list_expirations(symbol=symbol)
    # DataFrame with an 'expiration' column (str/date); keep >= start_year, <= today
    col = "expiration" if "expiration" in exps.columns else exps.columns[0]
    dates = [pd.Timestamp(x).date() for x in exps[col].tolist()]
    dates = sorted({d for d in dates if d.year >= start_year and d <= today})
    ed = _exp_dir(symbol, out_dir)
    ok = skip = empty = err = 0
    for i, exp in enumerate(dates, 1):
        f = ed / f"{exp:%Y%m%d}.parquet"
        e = ed / f"{exp:%Y%m%d}.empty"
        if f.exists() or e.exists():
            skip += 1
            continue
        start = max(dt.date(start_year, 1, 1), exp - dt.timedelta(days=window_days))
        try:
            raw = _greeks_with_retry(c, symbol, exp, start, strike_range)
            if raw is None or len(raw) == 0:
                e.touch(); empty += 1
                continue
            # the library returns tz-aware timestamps; the shared normalizer (also
            # used by the terminal path, kept byte-identical) expects tz-naive.
            raw = raw.copy()
            for tcol in ("underlying_timestamp", "timestamp"):
                if tcol in raw.columns and pd.api.types.is_datetime64_any_dtype(raw[tcol]):
                    raw[tcol] = pd.to_datetime(raw[tcol], utc=True).dt.tz_convert(
                        "America/New_York").dt.tz_localize(None)
            norm = normalize_greeks_eod(raw)
            if len(norm) == 0:
                e.touch(); empty += 1
            else:
                norm.to_parquet(f); ok += 1
                print(f"[{i}/{len(dates)}] {symbol} {exp} OK {len(norm)} rows", flush=True)
        except Exception as ex:
            err += 1
            print(f"[{i}/{len(dates)}] {symbol} {exp} ERR {str(ex)[:80]}", flush=True)
    print(f"{symbol} DONE pulls: ok={ok} skip={skip} empty={empty} err={err}", flush=True)
    return err


def concat(symbol, out_dir):
    symbol = symbol.upper()
    ed = _exp_dir(symbol, out_dir)
    files = sorted(ed.glob("*.parquet"))
    if not files:
        print(f"{symbol}: no per-expiration parquets to concat", flush=True)
        return
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df = df.sort_values(["date", "expiry", "strike", "right"]).reset_index(drop=True)
    dest = chain_path(symbol)
    df.to_parquet(dest)
    print(f"{symbol}: concat {len(files)} files -> {dest} | {df.shape} | "
          f"{df.date.min().date()}..{df.date.max().date()} | {df.expiry.nunique()} expiries", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--out-dir", default="data/options")
    ap.add_argument("--start-year", type=int, default=2024)
    ap.add_argument("--strike-range", type=int, default=15)
    ap.add_argument("--window-days", type=int, default=25)
    ap.add_argument("--concat", action="store_true")
    args = ap.parse_args()
    if args.concat:
        concat(args.symbol, args.out_dir)
    else:
        pull(args.symbol, args.out_dir, args.start_year, args.strike_range, args.window_days)


if __name__ == "__main__":
    main()
