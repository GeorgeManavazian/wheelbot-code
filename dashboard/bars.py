"""Daily OHLC for the dashboard, read through the engine's own bar source.

Not a second parquet reader: engine_v2.data.source owns that. This adds the two
things a view needs and the engine has no reason to — a cache, and a soft answer
for tickers with no bars."""
import pandas as pd
import streamlit as st

from src.engine_v2.data import source


def fetch_bars(ticker: str, start, end) -> pd.DataFrame:
    """Single-ticker OHLCV indexed by date. Empty frame if the ticker has no bars.

    The bars parquet holds 20 ETFs; the wheel page offers any ticker with an
    options chain on disk. XOP is the live example of the gap. The engine's
    loader raises KeyError there — a view wants to draw the rest of the page.
    """
    try:
        df = source.default_source().load([ticker], start, end)
    except KeyError:
        return pd.DataFrame()
    return df[ticker]


load_bars = st.cache_data(show_spinner=False)(fetch_bars)
