import pathlib
import pandas as pd

def test_engine_v2_importable():
    import src.engine_v2 as e
    assert e.__version__.startswith("2.")

def test_fixture_bars_exist_and_valid():
    p = pathlib.Path("fixtures/bars_2007_2010_small.parquet")
    assert p.exists()
    df = pd.read_parquet(p)
    assert set(["SPY", "TLT", "GLD"]).issubset(df.columns.get_level_values(0).unique())
    assert df.index.min() >= pd.Timestamp("2007-01-01")
    assert df.index.max() <= pd.Timestamp("2010-12-31")
    assert p.stat().st_size < 3_000_000

def test_fixture_regime_labels_align():
    bars = pd.read_parquet("fixtures/bars_2007_2010_small.parquet")
    reg = pd.read_parquet("fixtures/regime_labels_small.parquet")
    assert reg.index.equals(bars.index)
    assert set(reg.columns) == {"regime_trend", "regime_vol", "regime_rate"}
