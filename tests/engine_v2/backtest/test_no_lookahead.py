"""Verify orchestrator's per-asof slice actually prevents strategies from
reaching bars past asof. Uses observable side-effect."""
import pandas as pd
from src.engine_v2.backtest.orchestrator import run_backtest, BacktestConfig
from src.engine_v2.data.loader import load_bars


class _PeekTracker:
    """Records max index it ever sees in forecast."""
    display_name = "Peek Tracker"
    mechanism = "test-only"
    parameter_grid = {}
    holding_period_cap = 1
    max_index_seen = None

    def __init__(self): pass

    def forecast(self, bars, asof):
        # Record the LAST bar index the strategy is given access to.
        # If slicing is honored, this must never exceed asof.
        last = bars.index.max()
        cls = type(self)
        if cls.max_index_seen is None or last > cls.max_index_seen:
            cls.max_index_seen = last
        # Also record whether any bar > asof is visible
        future = bars.index[bars.index > asof]
        if len(future) > 0:
            cls.peeked_future = future[0]
        return pd.Series({"SPY": 10.0})


def test_orchestrator_never_shows_future_bars():
    _PeekTracker.max_index_seen = None
    _PeekTracker.peeked_future = None
    bars = load_bars(["SPY"], "2007-01-01", "2010-12-31")
    run_backtest(_PeekTracker, bars, config=BacktestConfig(n_folds=3, cpcv_k=2))
    # If lookahead were possible, peeked_future would be non-None.
    assert _PeekTracker.peeked_future is None, (
        f"Strategy saw future bar at {_PeekTracker.peeked_future}"
    )
    # And the highest index seen must be within the requested bars range
    assert _PeekTracker.max_index_seen <= bars.index.max()
