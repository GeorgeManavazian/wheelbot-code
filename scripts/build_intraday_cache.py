"""Build a clean intraday cache from local Databento 1-minute parquets.

Source files live OUTSIDE the repo (default ~/Documents/Trading/data) and are
never committed. This writes a gitignored full cache plus a small committed
fixture for tests. Run: python scripts/build_intraday_cache.py
"""
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd

TICKERS = ["SPY", "QQQ"]
SRC_TEMPLATE = "{ticker}_1m_2021-07-01_2026-07-03_ARCX.parquet"
CACHE_PATH = "data/intraday/intraday_1m.parquet"
FIXTURE_PATH = "fixtures/intraday_1m_small.parquet"
RTH_START, RTH_END = "09:30", "15:59"
_RENAME = {"open": "Open", "high": "High", "low": "Low",
           "close": "Close", "volume": "Volume"}

def normalize_ticker(df: pd.DataFrame, ticker: str,
                     include_extended: bool = False) -> pd.DataFrame:
    """RTH filter + capitalize OHLCV + tz-naive ET + MultiIndex (ticker, field)."""
    et = df.index.tz_convert("America/New_York")
    if not include_extended:
        t = et.time
        mask = (t >= pd.Timestamp(RTH_START).time()) & (t <= pd.Timestamp(RTH_END).time())
        df = df[mask]
        et = df.index.tz_convert("America/New_York")
    out = df.rename(columns=_RENAME)[list(_RENAME.values())].copy()
    out.index = et.tz_localize(None)
    out.columns = pd.MultiIndex.from_product([[ticker], out.columns])
    return out

def build(source_dir: str, include_extended: bool = False) -> pd.DataFrame:
    frames = []
    for ticker in TICKERS:
        p = Path(source_dir).expanduser() / SRC_TEMPLATE.format(ticker=ticker)
        if not p.exists():
            raise FileNotFoundError(f"missing source parquet: {p} (set --source-dir)")
        frames.append(normalize_ticker(pd.read_parquet(p), ticker, include_extended))
    return pd.concat(frames, axis=1).sort_index()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", default="~/Documents/Trading/data")
    ap.add_argument("--include-extended", action="store_true")
    args = ap.parse_args()

    out = build(args.source_dir, args.include_extended)
    Path("data/intraday").mkdir(parents=True, exist_ok=True)
    out.to_parquet(CACHE_PATH)

    days = pd.Index(out.index.normalize().unique())[:5]
    fixture = out[out.index.normalize().isin(days)]
    Path("fixtures").mkdir(exist_ok=True)
    fixture.to_parquet(FIXTURE_PATH)
    print(f"cache {CACHE_PATH}: {out.shape}; fixture {FIXTURE_PATH}: {fixture.shape}")

if __name__ == "__main__":
    main()
