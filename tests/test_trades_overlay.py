import pandas as pd
import pytest

from dashboard import theme, trades


@pytest.mark.parametrize("pnl, outcome, expected", [
    (-5.0, "Took profit", "Lost money"),
    (-5.0, "Assigned", "Lost money"),        # pnl sign wins — matches _style_blotter's branch order
    (100.0, "Assigned", "Assigned"),
    (100.0, "Took profit", "Kept premium"),
    (0.0, "Expired worthless", "Kept premium"),
    (float("nan"), "Open", "Open"),
])
def test_outcome_group(pnl, outcome, expected):
    assert trades.outcome_group(pnl, outcome) == expected


@pytest.mark.parametrize("pnl", [-5.0, 0.0, 100.0, float("nan")])
@pytest.mark.parametrize("outcome", [
    "Took profit", "Rolled", "Stopped", "Expired worthless", "Assigned",
    "Called away", "Liquidated", "Settled at mark", "Open",
])
def test_every_group_has_a_colour(pnl, outcome):
    """Guards the real failure mode: a group with no colour is a KeyError at render."""
    assert theme.GROUP_COLORS[trades.outcome_group(pnl, outcome)]
