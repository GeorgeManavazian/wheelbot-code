import pandas as pd
import pytest
from src.engine_v2.data.source import intraday_source, INTRADAY_FIXTURE_PATH
from src.engine_v2.backtest.simple import run_simple
from src.engine_v2.strategy.counter_trend import CounterTrendDipBuy as Strat

def _src():
    return intraday_source(INTRADAY_FIXTURE_PATH)

def test_intraday_source_lists_spy_qqq():
    src = _src()
    assert set(src.available_tickers()) == {"SPY", "QQQ"}
    lo, hi = src.date_range()
    assert lo.tz is None and hi.tz is None
    assert lo.year == 2021

def test_intraday_source_load_slices_by_timestamp():
    src = _src()
    lo, hi = src.date_range()
    bars = src.load(["SPY"], lo, hi)          # real timestamps -> last day included
    assert ("SPY", "Close") in bars.columns
    assert len(bars) > 300                    # multiple RTH days of minutes

def test_run_simple_on_intraday_is_frequency_aware():
    src = _src()
    lo, hi = src.date_range()
    bars = src.load(["SPY", "QQQ"], lo, hi)
    res = run_simple(Strat, bars, params={})
    # 1-min RTH bars -> ~98k periods/yr, NOT 252
    assert res.periods_per_year > 90_000
    assert res.equity.nunique() > 1           # equity actually moves
    assert not pd.isna(res.sharpe)
