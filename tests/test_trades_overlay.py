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


BLOTTER_COLS = ["opened", "closed", "instrument", "strike", "expiry", "qty",
                "credit", "outcome", "cost_to_close", "realized_pnl",
                "pct_of_credit", "days_held", "campaign_id"]

WINDOW_END = pd.Timestamp("2024-03-28")


def _row(**kw):
    base = dict(opened=pd.NaT, closed=pd.NaT, instrument="PUT", strike=450.0,
                expiry=pd.NaT, qty=1, credit=100.0, outcome="Took profit",
                cost_to_close=0.0, realized_pnl=100.0, pct_of_credit=1.0,
                days_held=14, campaign_id=0)
    base.update(kw)
    return base


def _blotter(rows):
    return pd.DataFrame(rows, columns=BLOTTER_COLS)


def test_option_row_draws_at_its_strike_between_open_and_close():
    b = _blotter([_row(opened=pd.Timestamp("2024-01-05"),
                       closed=pd.Timestamp("2024-01-19"), strike=450.0)])
    out = trades.overlay_frame(b, WINDOW_END)
    assert len(out) == 1
    assert out.loc[0, "y"] == 450.0
    assert out.loc[0, "x0"] == pd.Timestamp("2024-01-05")
    assert out.loc[0, "x1"] == pd.Timestamp("2024-01-19")
    assert not out.loc[0, "open_ended"]


def test_shares_row_draws_at_its_assignment_basis_and_is_kept():
    """SHARES rows carry strike=assignment price (report.py:347). No NaN case."""
    b = _blotter([_row(opened=pd.Timestamp("2024-02-01"),
                       closed=pd.Timestamp("2024-02-20"), instrument="SHARES",
                       strike=440.0, outcome="Called away", realized_pnl=-50.0)])
    out = trades.overlay_frame(b, WINDOW_END)
    assert len(out) == 1
    assert out.loc[0, "y"] == 440.0
    assert out.loc[0, "group"] == "Lost money"


def test_open_shares_run_to_window_end():
    b = _blotter([_row(opened=pd.Timestamp("2024-03-01"), closed=pd.NaT,
                       instrument="SHARES", strike=430.0, outcome="Open",
                       realized_pnl=float("nan"))])
    out = trades.overlay_frame(b, WINDOW_END)
    assert out.loc[0, "x1"] == WINDOW_END
    assert out.loc[0, "open_ended"]
    assert out.loc[0, "group"] == "Open"


def test_settled_at_mark_is_open_ended_too():
    """Regression guard: two outcomes leave closed=NaT. Matching the outcome
    string instead of closed.isna() truncates this one silently."""
    b = _blotter([_row(opened=pd.Timestamp("2024-03-05"), closed=pd.NaT,
                       strike=435.0, outcome="Settled at mark",
                       realized_pnl=80.0)])
    out = trades.overlay_frame(b, WINDOW_END)
    assert out.loc[0, "x1"] == WINDOW_END
    assert out.loc[0, "open_ended"]
    assert out.loc[0, "group"] == "Kept premium"


def test_empty_blotter_yields_empty_frame_with_columns():
    out = trades.overlay_frame(_blotter([]), WINDOW_END)
    assert out.empty
    assert list(out.columns) == ["x0", "x1", "y", "group", "open_ended", "hover"]


def test_hover_names_the_instrument_and_outcome():
    b = _blotter([_row(opened=pd.Timestamp("2024-01-05"),
                       closed=pd.Timestamp("2024-01-19"), outcome="Assigned",
                       realized_pnl=100.0, campaign_id=3)])
    hover = trades.overlay_frame(b, WINDOW_END).loc[0, "hover"]
    assert "PUT" in hover and "Assigned" in hover and "450" in hover
