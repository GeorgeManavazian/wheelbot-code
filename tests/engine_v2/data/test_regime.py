import pandas as pd
from src.engine_v2.data.loader import load_bars
from src.engine_v2.data.regime import tag_regime, REGIME_COLS

def test_regime_columns():
    bars = load_bars(["SPY"], "2007-01-01", "2010-12-31")
    r = tag_regime(bars, benchmark="SPY")
    assert tuple(r.columns) == REGIME_COLS

def test_bear_at_2008_10_09():
    bars = load_bars(["SPY"], "2007-01-01", "2010-12-31")
    r = tag_regime(bars, benchmark="SPY")
    assert r.loc["2008-10-09", "regime_trend"] == "bear"

def test_bull_at_2010_04_01():
    bars = load_bars(["SPY"], "2007-01-01", "2010-12-31")
    r = tag_regime(bars, benchmark="SPY")
    assert r.loc["2010-04-01", "regime_trend"] == "bull"

def test_high_vol_during_gfc_peak():
    bars = load_bars(["SPY"], "2007-01-01", "2010-12-31")
    r = tag_regime(bars, benchmark="SPY")
    peak = r.loc["2008-10-01":"2008-11-30", "regime_vol"]
    assert (peak == "high").mean() > 0.8

def test_index_aligns_with_bars():
    bars = load_bars(["SPY"], "2007-01-01", "2010-12-31")
    r = tag_regime(bars, benchmark="SPY")
    assert r.index.equals(bars.index)
