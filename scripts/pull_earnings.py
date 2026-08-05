"""Pull the earnings calendar for the trading universe into data/earnings/.

Source is yfinance (`get_earnings_dates`), which returns ~100 rows per name
spanning roughly 2002 to the next scheduled print — past AND future from one
call, so the same cache serves the backtest and the live gate.

Schwab cannot serve this. `get_instruments(..., Projection.FUNDAMENTAL)` was
checked on 2026-08-03 and carries 56 fields including nextDividendDate and beta,
but no earnings date of any kind.

Two artifacts, deliberately separate (spec 2026-08-03-earnings-blackout-design):

  calendar.parquet  ticker, earnings_date        -- the dates themselves
  status.parquet    ticker, status, n_dates, pulled_at

The status file exists because "this ETF has no earnings" and "the lookup for
this equity failed" both present as an empty calendar, and collapsing them is
how a gate stops existing without anyone noticing (the zombie_check failure
class, audit 2026-07-29). yfinance separates them for us: a name with no
earnings returns None, a broken lookup raises.

  ok      dates were returned
  none    the vendor says this name has no earnings (ETF, or delisted)
  failed  the lookup raised -- the gate is BLIND to this ticker

`none` does not distinguish a fund from a delisted equity. That distinction is
reporting polish, not a safety property: both are correctly "no scheduled print
to dodge", and delisted names are already handled by universe._RETIRED.

Resumable by default -- a ticker already recorded ok/none is skipped, so a
rate-limited run can simply be re-run. `--refresh` re-pulls everything.

Usage:
    python scripts/pull_earnings.py                  # resume, whole universe
    python scripts/pull_earnings.py --refresh        # re-pull everything
    python scripts/pull_earnings.py AAPL MSFT        # named tickers only
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import warnings

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT_DIR = os.path.join("data", "earnings")
CALENDAR = os.path.join(OUT_DIR, "calendar.parquet")
STATUS = os.path.join(OUT_DIR, "status.parquet")

LIMIT = 100          # rows per name; ~100 covers 2002 -> next scheduled print
RETRIES = 3
BACKOFF = 2.0        # seconds, doubled per retry
PACE = 0.4           # between tickers, to stay under the vendor's rate limit


def _fetch(ticker: str):
    """(dates, status). dates is a list of tz-naive ET dates, possibly empty."""
    import yfinance as yf

    for attempt in range(RETRIES):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df = yf.Ticker(ticker).get_earnings_dates(limit=LIMIT)
        except Exception as e:                       # noqa: BLE001 -- vendor raises broadly
            if attempt == RETRIES - 1:
                return [], f"failed: {type(e).__name__}: {e}"[:200]
            time.sleep(BACKOFF * (2 ** attempt))
            continue

        if df is None or not len(df):
            # The vendor's own "no earnings dates found" path. Not an error.
            return [], "none"

        idx = pd.DatetimeIndex(df.index)
        # Timestamps are tz-aware ET and carry the BMO/AMC signal in the clock
        # (AAPL prints 16:00 = after the close). We deliberately drop the time
        # and widen the blackout window by a day instead -- see the spec.
        if idx.tz is not None:
            idx = idx.tz_convert("America/New_York").tz_localize(None)
        dates = sorted({d.normalize() for d in idx})
        return dates, "ok"

    return [], "failed: exhausted retries"


def _load_prior():
    if os.path.exists(CALENDAR) and os.path.exists(STATUS):
        return pd.read_parquet(CALENDAR), pd.read_parquet(STATUS)
    cal = pd.DataFrame(columns=["ticker", "earnings_date"])
    st = pd.DataFrame(columns=["ticker", "status", "n_dates", "pulled_at"])
    return cal, st


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tickers", nargs="*", help="tickers (default: whole universe)")
    ap.add_argument("--refresh", action="store_true",
                    help="re-pull tickers already recorded ok/none")
    args = ap.parse_args()

    if args.tickers:
        universe = [t.upper() for t in args.tickers]
    else:
        from live.universe import UNIVERSE
        universe = list(UNIVERSE)

    os.makedirs(OUT_DIR, exist_ok=True)
    cal, st = _load_prior()

    done = set()
    if not args.refresh and len(st):
        done = set(st.loc[st["status"].isin(["ok", "none"]), "ticker"])

    todo = [t for t in universe if t not in done]
    print(f"{len(universe)} tickers, {len(done)} already cached, {len(todo)} to pull")

    rows, statuses = [], []
    counts = {"ok": 0, "none": 0, "failed": 0}
    for i, tk in enumerate(todo, 1):
        dates, status = _fetch(tk)
        kind = status.split(":")[0]
        counts[kind] = counts.get(kind, 0) + 1
        rows.extend({"ticker": tk, "earnings_date": d} for d in dates)
        statuses.append({"ticker": tk, "status": status, "n_dates": len(dates),
                         "pulled_at": pd.Timestamp.utcnow().tz_localize(None)})
        if kind == "failed" or i % 25 == 0 or i == len(todo):
            # flush: redirected to a log, stdout is block-buffered, so progress
            # is invisible for thousands of lines while stderr (the vendor's own
            # chatter) flows freely -- reads as a stall when it is not one.
            print(f"  [{i}/{len(todo)}] {tk}: {status} ({len(dates)} dates)",
                  flush=True)
        time.sleep(PACE)

    # concat only when the prior frame has rows: concatenating onto an empty
    # frame built from bare column names drags every dtype to object.
    if rows:
        fresh = pd.DataFrame(rows)
        keep = cal[~cal["ticker"].isin(fresh["ticker"])] if len(cal) else None
        cal = pd.concat([keep, fresh], ignore_index=True) if keep is not None and len(keep) else fresh
        cal["earnings_date"] = pd.to_datetime(cal["earnings_date"])
        cal = cal.sort_values(["ticker", "earnings_date"]).reset_index(drop=True)
        cal.to_parquet(CALENDAR, index=False)

    if statuses:
        fresh_st = pd.DataFrame(statuses)
        keep_st = st[~st["ticker"].isin(fresh_st["ticker"])] if len(st) else None
        st = pd.concat([keep_st, fresh_st], ignore_index=True) if keep_st is not None and len(keep_st) else fresh_st
        st = st.sort_values("ticker").reset_index(drop=True)
        st.to_parquet(STATUS, index=False)

    print(f"\nthis run: ok={counts['ok']} none={counts['none']} failed={counts['failed']}")
    if len(st):
        blind = st.loc[st["status"].str.startswith("failed"), "ticker"].tolist()
        print(f"cache totals: {len(cal)} dates over "
              f"{cal['ticker'].nunique() if len(cal) else 0} tickers")
        if blind:
            # Loud, because the gate cannot see these names at all.
            print(f"BLIND ({len(blind)}) -- re-run to retry: {', '.join(blind[:20])}"
                  + (" ..." if len(blind) > 20 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
