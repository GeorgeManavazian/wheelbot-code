import numpy as np
import pandas as pd
import pytest

from src.batch.runner import expand_grid, luck_warning, plateau_table, run_batch
from src.engine.backtest import BacktestConfig
from src.strategies.base import Strategy
from src.strategies.ts_trend import TSTrend


def make_long(n_days=300):
    idx = pd.bdate_range("2020-01-01", periods=n_days)
    rows = []
    for t, drift in (("UP", 0.001), ("DOWN", -0.0005)):
        px = 100 * np.cumprod(1 + np.full(n_days, drift))
        for d, p in zip(idx, px):
            rows.append({"date": d, "ticker": t, "open": p, "high": p,
                         "low": p, "close": p, "volume": 1000})
    return pd.DataFrame(rows)


def test_expand_grid_cartesian_product():
    strats = expand_grid(TSTrend, {"lookback": [50, 100, 150]})
    assert len(strats) == 3
    assert sorted(s.params["lookback"] for s in strats) == [50, 100, 150]


def test_run_batch_one_row_per_strategy():
    lb = run_batch(expand_grid(TSTrend, {"lookback": [20, 40]}), make_long(),
                   BacktestConfig(slippage_bps=0))
    assert len(lb) == 2
    assert {"label", "sharpe", "cagr", "n_trades", "lookback"} <= set(lb.columns)


def test_run_batch_survives_crashing_strategy():
    class Crasher(Strategy):
        name = "crasher"
        DEFAULTS = {}
        def target_weights(self, window):
            raise RuntimeError("boom")
    lb = run_batch([Crasher(), TSTrend(lookback=20)], make_long(),
                   BacktestConfig(slippage_bps=0))
    assert len(lb) == 2
    crashed = lb[lb["name"] == "crasher"].iloc[0]
    assert "boom" in crashed["error"]
    assert pd.isna(crashed["sharpe"])


def test_luck_warning_grows_with_n():
    w10, w1000 = luck_warning(10, 11), luck_warning(1000, 11)
    def sharpe_of(w):
        return float(w.split("~")[1].split()[0])
    assert sharpe_of(w1000) > sharpe_of(w10)


def test_plateau_table_pivots_param_vs_metric():
    lb = run_batch(expand_grid(TSTrend, {"lookback": [20, 40, 60]}), make_long(),
                   BacktestConfig(slippage_bps=0))
    tab = plateau_table(lb, "ts_trend", "lookback")
    assert list(tab.columns) == [20, 40, 60]


def test_run_batch_streams_incremental_csv(tmp_path):
    out = tmp_path / "lb.csv"
    lb = run_batch(expand_grid(TSTrend, {"lookback": [20, 40]}), make_long(),
                   BacktestConfig(slippage_bps=0), out_csv=out)
    streamed = pd.read_csv(out)
    streamed["error"] = streamed["error"].fillna("")
    assert len(streamed) == 2
    assert set(lb.columns) == set(streamed.columns)
    # streamed rows must equal the final leaderboard rows (order aside)
    a = streamed.sort_values("label").reset_index(drop=True)[sorted(lb.columns)]
    b = lb.sort_values("label").reset_index(drop=True)[sorted(lb.columns)]
    pd.testing.assert_frame_equal(a, b, check_dtype=False)


def test_luck_sharpe_extraction():
    from src.batch.runner import luck_sharpe, luck_warning
    import math
    # formula: sqrt(2*ln(max(n,2))/years)
    assert luck_sharpe(31, 11.0) == pytest.approx(
        math.sqrt(2 * math.log(31) / 11.0))
    assert luck_sharpe(1, 10.0) == luck_sharpe(2, 10.0)  # n clamped to 2
    # warning string still embeds the same number, unchanged format
    assert f"~{luck_sharpe(31, 11.0):.2f}" in luck_warning(31, 11.0)
