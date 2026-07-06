"""Friendly names: what the owner sees instead of technical labels."""
import pandas as pd

from dashboard.naming import friendly_label, friendly_map


def test_momentum_full_label():
    row = {"name": "momentum_rotation", "lookback": 63.0, "top_n": 5.0,
           "min_score": 20.0, "vol_window": 20.0}
    assert friendly_label(row) == "Momentum Rotation · 3mo · top 5 · min score 20"


def test_min_score_zero_hidden_and_vol_window_never_shown():
    row = {"name": "momentum_rotation", "lookback": 125.0, "top_n": 3.0,
           "min_score": 0.0, "vol_window": 20.0}
    assert friendly_label(row) == "Momentum Rotation · 6mo · top 3"


def test_ts_trend_and_unusual_lookback():
    assert friendly_label({"name": "ts_trend", "lookback": 125.0}) == \
        "Trend Following · 6mo"
    assert friendly_label({"name": "ts_trend", "lookback": 100.0}) == \
        "Trend Following · 100d"


def test_nan_params_skipped():
    # ts_trend rows in a mixed CSV carry NaN in momentum's param columns
    row = {"name": "ts_trend", "lookback": 63.0,
           "top_n": float("nan"), "min_score": float("nan")}
    assert friendly_label(row) == "Trend Following · 3mo"


def test_unknown_strategy_falls_back_to_title():
    assert friendly_label({"name": "dual_momentum", "lookback": 63.0}) == \
        "Dual Momentum · 3mo"


def test_friendly_map_disambiguates_collisions():
    ok = pd.DataFrame([
        {"label": "a", "name": "ts_trend", "lookback": 63.0},
        {"label": "b", "name": "ts_trend", "lookback": 63.0},
        {"label": "c", "name": "ts_trend", "lookback": 125.0},
    ])
    m = friendly_map(ok)
    assert m["a"] == "Trend Following · 3mo (a)"
    assert m["b"] == "Trend Following · 3mo (b)"
    assert m["c"] == "Trend Following · 6mo"
