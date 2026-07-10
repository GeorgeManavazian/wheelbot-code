import pandas as pd
import pytest
from src.engine_v2.backtest.simple import run_simple, Result
from src.engine_v2.backtest.orchestrator import BacktestConfig, position_history
from src.engine_v2.strategy.counter_trend import CounterTrendDipBuy as Strat

BARS = pd.read_parquet("fixtures/bars_2007_2010_small.parquet")

def test_run_simple_equity_matches_position_history():
    cfg = BacktestConfig()
    res = run_simple(Strat, BARS, config=cfg, params={})
    ref = position_history(Strat, {}, BARS, BARS.index, cfg)
    assert isinstance(res, Result)
    assert res.equity.iloc[-1] == pytest.approx(ref["equity"].iloc[-1])

def test_run_simple_has_gate_free_diagnostics():
    res = run_simple(Strat, BARS, params={})
    assert res.periods_per_year == pytest.approx(252, abs=6)
    assert not hasattr(res, "verdict")
    assert set(res.benchmarks) == {"SPY", "60_40"}
    assert res.trades > 0
    assert -1.0 <= res.max_drawdown <= 0.0

def test_run_simple_carries_recent_and_yearly_sharpe():
    # recent_start inside the fixture window (2007-2010) so the slice is non-empty
    res = run_simple(Strat, BARS, params={}, recent_start="2009-01-01")
    assert set(res.recent) == {"start", "cagr", "sharpe", "max_drawdown"}
    assert res.recent["start"] == "2009-01-01"
    assert len(res.yearly_sharpe) >= 1
    assert res.yearly_sharpe.index.min() >= 2007

def test_higher_spread_lowers_end_equity():
    lo = run_simple(Strat, BARS, config=BacktestConfig(spread_bps_per_side=0.0), params={})
    hi = run_simple(Strat, BARS, config=BacktestConfig(spread_bps_per_side=50.0), params={})
    assert hi.equity.iloc[-1] < lo.equity.iloc[-1]
