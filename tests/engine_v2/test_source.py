import pandas as pd
from src.engine_v2.data.source import ParquetSource, default_source, FIXTURE_PATH, UNIVERSE_PATH

def test_fixture_source_lists_tickers_and_range():
    src = ParquetSource(FIXTURE_PATH)
    assert set(src.available_tickers()) == {"SPY", "TLT", "GLD"}
    lo, hi = src.date_range()
    assert lo == pd.Timestamp("2007-01-03")
    assert hi == pd.Timestamp("2010-12-30")

def test_fixture_source_load_slices():
    src = ParquetSource(FIXTURE_PATH)
    bars = src.load(["SPY", "TLT"], "2008-01-01", "2008-12-31")
    assert set(bars.columns.get_level_values(0)) == {"SPY", "TLT"}
    assert bars.index.min() >= pd.Timestamp("2008-01-01")
    assert bars.index.max() <= pd.Timestamp("2008-12-31")

def test_default_source_is_real_universe():
    src = default_source()
    tk = set(src.available_tickers())
    assert {"SPY", "QQQ", "IWM", "TLT", "GLD"} <= tk
    assert len(tk) == 20
    lo, hi = src.date_range()
    assert lo == pd.Timestamp("2010-01-04")
    assert hi >= pd.Timestamp("2026-06-30")
