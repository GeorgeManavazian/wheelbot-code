import numpy as np
import pandas as pd
import pytest
from src.engine_v2.backtest import metrics_simple as m

def _daily(n=252, start="2010-01-01"):
    return pd.date_range(start, periods=n, freq="B")

def test_infer_periods_per_year_daily():
    idx = _daily(300)
    assert m.infer_periods_per_year(idx) == pytest.approx(252, abs=6)

def test_infer_periods_per_year_minute():
    idx = pd.date_range("2010-01-04 09:30", periods=400, freq="T")
    # ~390 trading minutes/day * 252 days
    assert m.infer_periods_per_year(idx) > 90_000

def test_cagr_and_maxdd_on_known_curve():
    idx = _daily(253)
    equity = pd.Series(np.linspace(100.0, 110.0, 253), index=idx)  # +10% over ~1y
    assert m.cagr(equity, 252) == pytest.approx(0.10, abs=0.02)
    assert m.max_drawdown(equity) == pytest.approx(0.0, abs=1e-9)

def test_sharpe_frequency_scales():
    idx = _daily(252)
    rets = pd.Series(np.full(252, 0.001), index=idx)  # constant positive, zero std
    # zero variance -> guarded to 0.0, not inf/nan
    assert m.sharpe(rets, 252) == 0.0

def test_yearly_returns_splits_by_year():
    idx = pd.date_range("2010-01-01", "2011-12-31", freq="B")
    equity = pd.Series(np.linspace(100, 121, len(idx)), index=idx)
    yr = m.yearly_returns(equity)
    assert set(yr.index) == {2010, 2011}

def test_yearly_sharpe_one_per_year():
    idx = pd.date_range("2010-01-01", "2011-12-31", freq="B")
    rng = np.random.default_rng(0)
    rets = pd.Series(rng.normal(0.0005, 0.01, len(idx)), index=idx)
    ys = m.yearly_sharpe(rets, 252)
    assert set(ys.index) == {2010, 2011}
    assert ys.notna().all()

def test_buy_hold_matches_hand_calc():
    idx = _daily(3)
    cols = pd.MultiIndex.from_tuples([("SPY", "Close")])
    bars = pd.DataFrame([[100.0], [110.0], [121.0]], index=idx, columns=cols)
    eq = m.buy_hold_equity(bars, {"SPY": 1.0}, 100_000.0)
    assert eq.iloc[-1] == pytest.approx(121_000.0)

def test_sharpe_too_short_is_nan():
    assert np.isnan(m.sharpe(pd.Series([], dtype=float), 252))
    assert np.isnan(m.sharpe(pd.Series([0.01]), 252))
