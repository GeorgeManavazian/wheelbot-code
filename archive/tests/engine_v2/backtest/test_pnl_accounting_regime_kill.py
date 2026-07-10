"""Parked 2026-07-10 (Task 6): split out of tests/engine_v2/backtest/test_pnl_accounting.py
because this is the only test in that file that depends on the gate
(src/engine_v2/gate, archived to archive/engine_v2_gate). The other 7 tests in
test_pnl_accounting.py test `_fold_trial_returns`/`position_history` directly and
stay live. This one is parked, not deleted, alongside the rest of the gate.

Note: the `src.engine_v2.gate.*` import below no longer resolves post-archive
(gate now lives at archive/engine_v2_gate). Left as-is for historical fidelity;
adjust the import path if this test is ever revived.
"""
import pandas as pd

from src.engine_v2.backtest.orchestrator import _fold_trial_returns, BacktestConfig


class _Fixed:
    """Constant forecast plugin. `_forecast` set per subclass."""
    display_name = "Fixed"
    mechanism = "test-only"
    parameter_grid: dict = {}
    holding_period_cap = 10_000  # effectively no time exit
    _forecast = 10.0

    def __init__(self):
        pass

    def forecast(self, bars, asof):
        return pd.Series({"SPY": self._forecast})


class ConstantLong(_Fixed):
    _forecast = 10.0


def test_regime_kill_fires_for_an_economic_reason_not_a_cost_artifact():
    """The 2026-07-09 decision note cited 'constant-long SPY triggers regime_kill'
    as proof the gate works. It proved nothing: with cost-only returns EVERY regime
    cell was negative, so the kill fired regardless of the data. Pin the real signal --
    buy-and-hold must EARN in bull regimes and LOSE in bear ones."""
    from src.engine_v2.data.loader import load_bars
    from src.engine_v2.data.regime import tag_regime
    from src.engine_v2.gate.regime_eval import per_regime_sharpe, regime_kill

    bars = load_bars(["SPY"], "2007-01-01", "2010-12-31")
    rets = _fold_trial_returns(ConstantLong, {}, bars, bars.index, BacktestConfig())
    tbl = per_regime_sharpe(rets, tag_regime(bars).reindex(rets.index).ffill())

    bull = tbl[tbl.index.str.startswith("bull")]["sharpe"]
    bear = tbl[tbl.index.str.startswith("bear")]["sharpe"]
    assert (bull > 0).all(), f"buy-and-hold lost money in a bull regime: {tbl}"
    assert (bear < 0).all(), f"buy-and-hold made money in a bear regime: {tbl}"
    assert regime_kill(tbl) is True
