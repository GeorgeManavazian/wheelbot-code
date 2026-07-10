"""One-shot: build ~2 MB fixture parquet for engine_v2 tests.
Uses yfinance so it's reproducible without Schwab creds.
Run: python scripts/build_fixtures.py
"""
from pathlib import Path
import pandas as pd
import yfinance as yf

TICKERS = ["SPY", "TLT", "GLD"]
START, END = "2007-01-01", "2010-12-31"

def main():
    Path("fixtures").mkdir(exist_ok=True)
    raw = yf.download(TICKERS, start=START, end=END, auto_adjust=True, group_by="ticker", progress=False)
    raw.to_parquet("fixtures/bars_2007_2010_small.parquet")
    spy_close = raw["SPY"]["Close"]
    spy_ma200 = spy_close.rolling(200).mean()
    regime = pd.DataFrame(index=raw.index)
    regime["regime_trend"] = (spy_close > spy_ma200).map({True: "bull", False: "bear"}).fillna("bull")
    daily_ret = spy_close.pct_change()
    realized_vol = daily_ret.rolling(20).std() * (252 ** 0.5)
    regime["regime_vol"] = (realized_vol > 0.20).map({True: "high", False: "calm"}).fillna("calm")
    regime["regime_rate"] = "falling"  # placeholder for fixture; real ingest computes from 10y-3mo
    regime.to_parquet("fixtures/regime_labels_small.parquet")

if __name__ == "__main__":
    main()
