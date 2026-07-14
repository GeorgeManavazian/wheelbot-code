import numpy as np
import pandas as pd
import pytest
from src.engine_v2.regime.data import closes_for
from src.engine_v2.regime.state import regime_series, describe

@pytest.fixture(scope="module")
def spy():
    return regime_series(closes_for("SPY"))

def test_known_regimes_on_real_spy(spy):
    assert spy.loc["2021-07-01", "trend"] == "uptrend"
    assert spy.loc["2021-07-01", "vol"] == "calm"
    assert spy.loc["2022-06-15", "trend"] == "downtrend"
    assert spy.loc["2020-03-20", "vol"] == "stressed"
    assert spy.loc["2020-03-20", "drawdown"] < -0.25

def test_warmup_dropped(spy):
    raw = closes_for("SPY")
    assert len(spy) <= len(raw) - 200

def test_no_look_ahead():
    closes = closes_for("SPY")
    for k in (500, 1500, 2500):
        full = regime_series(closes)
        part = regime_series(closes.iloc[:k])
        d = part.index[-1]
        pd.testing.assert_series_equal(full.loc[d], part.loc[d], check_names=False)

def test_short_series_returns_empty():
    s = pd.Series(np.linspace(100, 110, 150),
                  index=pd.bdate_range("2024-01-01", periods=150))
    assert regime_series(s).empty

def test_describe_reads_plainly(spy):
    txt = describe(spy.loc["2021-07-01"])
    assert "uptrend" in txt.lower() and "calm" in txt.lower() and "%" in txt
