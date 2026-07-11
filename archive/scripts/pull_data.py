"""Pull daily OHLCV for the ETF universe from yfinance into data/raw/.

auto_adjust=True gives split+dividend adjusted prices (total-return series).
We also save unadjusted SPY so QC can prove adjustment actually happened.
"""
from pathlib import Path

import yfinance as yf

from src.engine.universe import TICKERS

RAW_DIR = Path("data/raw")
START = "2010-01-01"


def clean(df):
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df.index.name = "date"
    return df.dropna(how="all")


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for ticker in TICKERS:
        df = yf.download(ticker, start=START, auto_adjust=True, progress=False)
        if df.empty:
            raise SystemExit(f"FATAL: no data for {ticker}")
        if isinstance(df.columns[0], tuple):  # yfinance MultiIndex quirk
            df.columns = df.columns.get_level_values(0)
        clean(df).to_parquet(RAW_DIR / f"{ticker}.parquet")
        print(f"{ticker}: {len(df)} rows, {df.index[0].date()} -> {df.index[-1].date()}")

    spy_raw = yf.download("SPY", start=START, auto_adjust=False, progress=False)
    if isinstance(spy_raw.columns[0], tuple):
        spy_raw.columns = spy_raw.columns.get_level_values(0)
    clean(spy_raw).to_parquet(RAW_DIR / "SPY_unadjusted.parquet")
    print("SPY_unadjusted saved (QC adjustment check)")


if __name__ == "__main__":
    main()
