import pandas as pd
from dashboard import sensitivity
from src.engine_v2.backtest.orchestrator import BacktestConfig
from src.engine_v2.data.source import default_source
from src.engine_v2.strategy.registry import STRATEGIES, get_strategy

def test_cost_sweep_returns_one_row_per_spread_level():
    src = default_source()
    lo, hi = src.date_range()
    bars = src.load(["SPY", "TLT"], lo, hi)
    strat = get_strategy(list(STRATEGIES)[0])
    out = sensitivity.cost_sweep(strat, bars, BacktestConfig(),
                                 spreads=(0.0, 5.0))
    assert list(out.columns) == ["spread_bps", "cagr", "sharpe"]
    assert out["spread_bps"].tolist() == [0.0, 5.0]
    assert out["cagr"].notna().all()

def test_higher_spread_never_helps():
    # More friction cannot raise CAGR. If it does, costs are not being charged.
    src = default_source()
    lo, hi = src.date_range()
    bars = src.load(["SPY", "TLT"], lo, hi)
    strat = get_strategy(list(STRATEGIES)[0])
    out = sensitivity.cost_sweep(strat, bars, BacktestConfig(),
                                 spreads=(0.0, 25.0))
    assert out.loc[1, "cagr"] <= out.loc[0, "cagr"] + 1e-9

def test_borrow_is_held_at_the_base_config_value():
    src = default_source()
    lo, hi = src.date_range()
    bars = src.load(["SPY", "TLT"], lo, hi)
    strat = get_strategy(list(STRATEGIES)[0])
    base = BacktestConfig(borrow_bps_annual=500.0)
    out = sensitivity.cost_sweep(strat, bars, base, spreads=(1.0,))
    # Same spread + same borrow as a direct run => identical CAGR.
    from src.engine_v2.backtest.simple import run_simple
    direct = run_simple(strat, bars,
                        config=BacktestConfig(spread_bps_per_side=1.0,
                                              borrow_bps_annual=500.0))
    assert out.loc[0, "cagr"] == direct.cagr
