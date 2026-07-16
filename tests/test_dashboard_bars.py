import os
import pandas as pd
import pytest

from dashboard import bars
from src.engine_v2.data import source

pytestmark = pytest.mark.skipif(
    not os.path.exists(source.UNIVERSE_PATH),
    reason="ETF universe parquet not on disk")

START = pd.Timestamp("2024-01-02")
END = pd.Timestamp("2024-03-28")


def test_fetch_bars_returns_ohlc_for_a_known_ticker():
    df = bars.fetch_bars("SPY", START, END)
    assert not df.empty
    assert {"Open", "High", "Low", "Close"} <= set(df.columns)
    assert df.index.min() >= START and df.index.max() <= END


def test_fetch_bars_unknown_ticker_returns_empty_not_raises():
    """XOP has an options chain but no bars. The page must degrade, not throw."""
    df = bars.fetch_bars("XOP", START, END)
    assert df.empty


def test_fetch_bars_columns_are_flat_not_multiindex():
    df = bars.fetch_bars("SPY", START, END)
    assert not isinstance(df.columns, pd.MultiIndex)
