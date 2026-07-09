import pytest
import pandas as pd
from src.engine_v2.data.loader import load_bars, PreModernEraError, MODERN_ERA_START

def test_modern_era_boundary_rejects_pre_2007():
    with pytest.raises(PreModernEraError):
        load_bars(["SPY"], start="2006-12-31", end="2008-01-01")

def test_load_bars_returns_multiindex_frame():
    df = load_bars(["SPY", "TLT"], start="2007-01-01", end="2010-12-31")
    assert isinstance(df.columns, pd.MultiIndex)
    assert "SPY" in df.columns.get_level_values(0)
    assert "Close" in df.columns.get_level_values(1)

def test_load_bars_missing_ticker_raises():
    with pytest.raises(KeyError, match="ZZZZ"):
        load_bars(["ZZZZ"], start="2007-01-01", end="2008-01-01")

def test_load_bars_fills_forward_false_by_default():
    df = load_bars(["SPY"], start="2007-01-01", end="2010-12-31")
    assert df["SPY"]["Close"].isna().sum() == 0, "no silent fill-forward; parquet should be clean"

def test_constant_export():
    assert MODERN_ERA_START == pd.Timestamp("2007-01-01")
