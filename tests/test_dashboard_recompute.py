"""Round-trip: leaderboard row -> strategy -> re-run -> identical metrics."""
import numpy as np
import pandas as pd
import pytest

from dashboard import recompute
from src.engine.backtest import run_backtest
from src.engine.metrics import summarize
from src.strategies.ts_trend import TSTrend


def synthetic_long_df(days=200, tickers=("AAA", "BBB"), seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2015-01-02", periods=days)
    frames = []
    for i, t in enumerate(tickers):
        close = pd.Series(
            100 * (1 + i) * np.cumprod(1 + rng.normal(0.0005, 0.01, days)),
            index=dates)
        frames.append(pd.DataFrame({
            "date": dates, "ticker": t,
            "open": close.shift(1).fillna(close.iloc[0]).values,
            "close": close.values}))
    return pd.concat(frames, ignore_index=True)


def make_row(strat, long_df):
    stats = summarize(run_backtest(long_df, strat))
    # CSV round-trip turns ints into floats — simulate that
    return {"label": strat.label(), "name": strat.name, "error": "",
            "lookback": float(strat.params["lookback"]), **stats}


def test_row_to_strategy_coerces_param_types():
    s = recompute.row_to_strategy({"name": "ts_trend", "lookback": 63.0})
    assert isinstance(s, TSTrend) and s.params["lookback"] == 63
    assert isinstance(s.params["lookback"], int)


def test_row_to_strategy_nan_param_uses_default():
    s = recompute.row_to_strategy({"name": "ts_trend", "lookback": float("nan")})
    assert s.params["lookback"] == TSTrend.DEFAULTS["lookback"]


def test_unknown_strategy_raises():
    with pytest.raises(recompute.UnknownStrategyError):
        recompute.row_to_strategy({"name": "deleted_plugin", "lookback": 63.0})


def test_round_trip_is_fresh_and_tampered_row_is_stale():
    long_df = synthetic_long_df()
    row = make_row(TSTrend(lookback=20), long_df)
    result = recompute.recompute_run(row, long_df)
    assert recompute.check_stale(row, result) == []
    tampered = {**row, "sharpe": row["sharpe"] + 0.1}
    problems = recompute.check_stale(tampered, result)
    assert problems and "sharpe" in problems[0]


def test_check_stale_nan_both_sides_not_stale(monkeypatch):
    """NaN on both sides must NOT fire a false stale alarm (math.isclose(nan,nan)==False)."""
    long_df = synthetic_long_df()
    row = make_row(TSTrend(lookback=20), long_df)
    row["sharpe"] = float("nan")
    # Patch summarize so the recomputed stats also have NaN sharpe
    nan_stats = {**row, "sharpe": float("nan")}
    monkeypatch.setattr(recompute, "summarize", lambda _: nan_stats)
    dummy_result = object()
    assert recompute.check_stale(row, dummy_result) == []
    # Non-NaN mismatch elsewhere still reports
    row2 = {**row, "cagr": row["cagr"] + 0.5}
    problems = recompute.check_stale(row2, dummy_result)
    assert problems and "cagr" in problems[0]
