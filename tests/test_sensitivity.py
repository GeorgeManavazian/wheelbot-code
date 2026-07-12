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

    # Guard against a vacuous test: if the strategy never trades in this
    # window, costs legitimately cannot bite regardless of whether the
    # engine is charging them, and any assertion below would be meaningless.
    from src.engine_v2.backtest.simple import run_simple
    sanity = run_simple(strat, bars, config=BacktestConfig())
    assert sanity.trades > 0, (
        "strategy did not trade in this window; a cost-sensitivity test "
        "is vacuous with zero turnover"
    )

    out = sensitivity.cost_sweep(strat, bars, BacktestConfig(),
                                 spreads=(0.0, 25.0))
    # Require a real economic gap between 0bps and 25bps, not just a bare
    # "<" that floating-point noise (or a rounding quirk) could satisfy.
    # Observed gap on this strategy/universe is ~5.9 percentage points
    # (0.0682 -> 0.0094); 1 percentage point is comfortably inside that
    # while still being large enough that it could only be explained by
    # costs actually being charged.
    MIN_CAGR_GAP = 0.01  # 1 percentage point
    gap = out.loc[0, "cagr"] - out.loc[1, "cagr"]
    assert gap >= MIN_CAGR_GAP, (
        f"expected 25bps spread to cost at least {MIN_CAGR_GAP:.2%} CAGR "
        f"vs 0bps, but observed gap was only {gap:.4%}"
    )

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
