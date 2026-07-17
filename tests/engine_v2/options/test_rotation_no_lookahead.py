import pandas as pd
from src.engine_v2.options.portfolio import _row_before
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for


def test_row_before_is_strictly_prior_day():
    # _row_before(d) must return a state dated strictly before d — never d itself.
    states = regime_series(closes_for("SPY"))
    probe = states.index[500]
    row = _row_before(states, probe)
    assert row is not None
    assert states.index[states.index.get_loc(probe) - 1] <= probe
    # the returned row's own date is strictly < probe
    got_date = states.index[states.index.searchsorted(probe) - 1]
    assert got_date < probe
