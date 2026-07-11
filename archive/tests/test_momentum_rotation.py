import numpy as np
import pandas as pd
import pytest
from scipy import stats

from src.strategies.momentum_rotation import MomentumRotation, momentum_score


def geometric_series(daily_ret, n, start=100.0):
    return pd.Series(start * (1 + daily_ret) ** np.arange(n))


def test_momentum_score_matches_hand_computed_linregress():
    s = geometric_series(0.001, 60)
    y = np.log(s.values)
    slope, _, r, _, _ = stats.linregress(np.arange(60), y)
    expected = (np.exp(slope) ** 252 - 1) * 100 * r**2
    assert momentum_score(s) == pytest.approx(expected)


def test_momentum_score_perfect_uptrend_positive():
    assert momentum_score(geometric_series(0.001, 60)) > 0


def test_momentum_score_downtrend_negative():
    assert momentum_score(geometric_series(-0.001, 60)) < 0


def make_window(n_days, rets: dict, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=n_days)
    return pd.DataFrame(
        {t: geometric_series(r, n_days).values for t, r in rets.items()}, index=idx)


def _month_boundary_after(window, min_rows):
    """Truncate so the window ends on a month's first trading day AND has
    at least min_rows history (so lookback checks pass)."""
    months = window.index.month
    for i in range(min_rows, len(window)):
        if months[i] != months[i - 1]:
            return window.iloc[: i + 1]
    raise AssertionError("no month boundary after min_rows — enlarge window")


def test_selects_top_n_by_momentum():
    w = _month_boundary_after(make_window(
        80, {"HOT": 0.002, "WARM": 0.001, "COLD": -0.001, "MEH": 0.0002}), 41)
    weights = MomentumRotation(lookback=40, top_n=2).target_weights(w)
    assert set(weights) == {"HOT", "WARM"}
    assert sum(weights.values()) == pytest.approx(1.0)


def test_min_score_leaves_cash():
    w = _month_boundary_after(make_window(80, {"A": -0.001, "B": -0.002}), 41)
    weights = MomentumRotation(lookback=40, top_n=2, min_score=0.0).target_weights(w)
    assert weights == {}


def test_inverse_vol_weights_favor_calm_asset():
    # equal trend, different noise -> calmer asset gets more weight
    n = 80
    idx = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(7)
    base = 0.001 + rng.normal(0, 0.001, n)
    calm = 100 * np.cumprod(1 + base)
    wild = 100 * np.cumprod(1 + base + rng.normal(0, 0.02, n))
    w = _month_boundary_after(pd.DataFrame({"CALM": calm, "WILD": wild}, index=idx), 41)
    weights = MomentumRotation(lookback=40, top_n=2, min_score=-1000).target_weights(w)
    assert weights["CALM"] > weights["WILD"]


def test_none_outside_month_start():
    w = make_window(50, {"A": 0.001})
    assert w.index[-1].month == w.index[-2].month
    assert MomentumRotation(lookback=20).target_weights(w) is None
