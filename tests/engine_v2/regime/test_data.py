import os
import pandas as pd
import pytest
from src.engine_v2.regime.data import closes_for
from src.engine_v2.options.data import chain_path

def test_spy_comes_from_long_bars():
    s = closes_for("SPY")
    assert s.index.min().year <= 2010 and len(s) > 3000
    assert s.name == "SPY" and s.index.is_monotonic_increasing

@pytest.mark.skipif(not os.path.exists(chain_path("GDX")),
                    reason="GDX chain data not pulled (gitignored)")
def test_gdx_falls_back_to_chain_underlying():
    s = closes_for("GDX")   # not in the bars fixture
    assert s.index.min().year >= 2017 and len(s) > 1000

def test_unknown_symbol_raises():
    with pytest.raises(FileNotFoundError):
        closes_for("ZZZTOP")
