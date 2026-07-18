"""Schwab JSON -> engine-native shapes. Pure mapping functions (offline-testable)
plus thin client-fetch wrappers. Data-only; no order code. py3.12 (.venv-live)."""
from __future__ import annotations
import datetime as dt
import pandas as pd

_CHAIN_COLS = ["date", "expiry", "strike", "right", "dte",
               "delta", "bid", "ask", "mid", "underlying"]


def closes_from_json(payload: dict) -> pd.Series:
    """price_history JSON -> daily close Series (normalized-date index, sorted,
    de-duplicated keep-last). Feeds regime_series()."""
    candles = payload.get("candles", []) or []
    if not candles:
        return pd.Series([], dtype=float, name="close")
    df = pd.DataFrame(candles)
    idx = pd.to_datetime(df["datetime"], unit="ms").dt.normalize()
    s = pd.Series(df["close"].astype(float).to_numpy(), index=idx, name="close")
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


def daily_closes(client, ticker: str) -> pd.Series:
    r = client.get_price_history_every_day(ticker)
    if r.status_code != 200:
        raise RuntimeError(f"{ticker} price_history -> HTTP {r.status_code}")
    return closes_from_json(r.json())
