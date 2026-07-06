import numpy as np
import pandas as pd
import pytest

from src.strategies.ts_trend import TSTrend


def make_window(n_days, trend_cols, flat_cols, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=n_days)
    data = {}
    for c in trend_cols:
        data[c] = np.linspace(100, 200, n_days)   # rising
    for c in flat_cols:
        data[c] = np.linspace(100, 80, n_days)    # falling
    return pd.DataFrame(data, index=idx)


def test_none_when_not_month_start():
    w = make_window(50, ["A"], ["B"])
    # pick a window whose last two dates share a month
    assert w.index[-1].month == w.index[-2].month
    assert TSTrend(lookback=20).target_weights(w) is None


def _first_month_boundary(window):
    """Truncate window so it ends on the first day of a new month."""
    months = window.index.month
    for i in range(1, len(window)):
        if months[i] != months[i - 1]:
            return window.iloc[: i + 1]
    raise AssertionError("no month boundary in window")


def test_longs_rising_skips_falling_on_month_start():
    w = _first_month_boundary(make_window(60, ["A"], ["B"]))
    weights = TSTrend(lookback=20).target_weights(w)
    assert weights == {"A": 0.5}   # 1/2 columns; B falling -> cash


def test_all_cash_when_everything_falls():
    w = _first_month_boundary(make_window(60, [], ["A", "B"]))
    assert TSTrend(lookback=20).target_weights(w) == {}


def test_none_when_history_shorter_than_lookback():
    w = _first_month_boundary(make_window(60, ["A"], []))
    assert TSTrend(lookback=500).target_weights(w) is None


def test_weights_never_exceed_one():
    w = _first_month_boundary(make_window(60, ["A", "B", "C"], []))
    weights = TSTrend(lookback=20).target_weights(w)
    assert sum(weights.values()) <= 1.0 + 1e-9
