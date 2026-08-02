"""A8 (rescoped 2026-08-02): early assignment is NOT modelled -- paper mode
simulates its own fills, so it structurally cannot happen there. The defense
for future real-money mode is `diff_positions`: given what the broker says an
account holds and what the bot's state says, return every mismatch, classified
per the A8 taxonomy (T1-T7). Freeze semantics reuse the A10 idiom; the engine
wiring lands with the real-money build. These tests pin the pure function.
"""
import pandas as pd
import pytest

from src.engine_v2.options.chain import Contract
from src.engine_v2.options.portfolio import PortfolioState
from live.reconcile import diff_positions, DEFAULT_CASH_EPSILON


def _pos(ticker, shares=0, phase="PUT", basis=None, short=None):
    return {"ticker": ticker, "shares": shares, "phase": phase, "basis": basis,
            "premium": 0.0, "campaign": 1, "last_spot": 100.0, "short": short}


def _short(root, strike, right, n, expiry="2026-08-21"):
    return {"contract": Contract(root, pd.Timestamp(expiry), strike, right),
            "contracts": n, "credit": 1.0, "last_mid": 0.5}


def _key(root, strike, right, expiry="2026-08-21"):
    return (root, expiry, float(strike), right)


def _state(positions, cash=50_000.0):
    return PortfolioState(cash=cash, positions=positions)


def _view(cash, options=None, equity=None):
    return {"cash": cash, "options": dict(options or {}), "equity": dict(equity or {})}


def test_clean_book_returns_no_divergences():
    st = _state([_pos("XYZ", short=_short("XYZ", 100.0, "P", 3))])
    bv = _view(st.cash, options={_key("XYZ", 100.0, "P"): -3})
    assert diff_positions(bv, st) == []


def test_t1_full_put_assignment_freezes():
    """Short put gone at the broker, 100*n shares appeared: the early
    assignment the sim can never produce. FREEZE, never auto-apply."""
    st = _state([_pos("XYZ", short=_short("XYZ", 100.0, "P", 3))])
    bv = _view(st.cash - 100.0 * 100 * 3,
               options={}, equity={"XYZ": 300})
    div = diff_positions(bv, st)
    assert len(div) == 1
    d = div[0]
    assert d.kind == "early_put_assignment"
    assert d.subject == "XYZ"
    assert d.severity == "FREEZE"


def test_t2_partial_put_assignment_freezes():
    """k of n contracts assigned: broker shows n-k short plus 100*k shares."""
    st = _state([_pos("XYZ", short=_short("XYZ", 100.0, "P", 3))])
    bv = _view(st.cash - 100.0 * 100 * 2,
               options={_key("XYZ", 100.0, "P"): -1}, equity={"XYZ": 200})
    div = diff_positions(bv, st)
    assert [d.kind for d in div] == ["early_put_assignment_partial"]
    assert div[0].severity == "FREEZE"


def test_t3_early_call_assignment_freezes():
    """Covered shares called away pre-ex-div: shares gone, short call gone."""
    st = _state([_pos("XYZ", shares=500, phase="CALL", basis=95.0,
                      short=_short("XYZ", 110.0, "C", 5))])
    bv = _view(st.cash + 110.0 * 100 * 5, options={}, equity={})
    div = diff_positions(bv, st)
    assert [d.kind for d in div] == ["early_call_assignment"]
    assert div[0].severity == "FREEZE"


def test_t4_small_cash_drift_alerts_only():
    """Positions match, cash differs by less than epsilon: the A13 fee class.
    ALERT, no freeze."""
    st = _state([_pos("XYZ", short=_short("XYZ", 100.0, "P", 3))])
    bv = _view(st.cash - 24.99, options={_key("XYZ", 100.0, "P"): -3})
    div = diff_positions(bv, st)
    assert [(d.kind, d.severity) for d in div] == [("cash_drift", "ALERT")]


def test_t5_cash_drift_at_epsilon_freezes():
    """At/above epsilon the drift is no longer explicable as fees."""
    st = _state([_pos("XYZ", short=_short("XYZ", 100.0, "P", 3))])
    bv = _view(st.cash - DEFAULT_CASH_EPSILON,
               options={_key("XYZ", 100.0, "P"): -3})
    div = diff_positions(bv, st)
    assert [(d.kind, d.severity) for d in div] == [("cash_drift", "FREEZE")]


def test_cash_identical_to_the_cent_is_clean():
    st = _state([])
    assert diff_positions(_view(st.cash), st) == []
    assert diff_positions(_view(st.cash + 0.004), st) == []


def test_t6_unknown_option_position_freezes():
    """Broker holds a leg the state never opened (manual trade, or an
    OCC-adjusted symbol after a corporate action)."""
    st = _state([])
    bv = _view(st.cash, options={_key("ABC", 50.0, "P"): -2})
    div = diff_positions(bv, st)
    assert [d.kind for d in div] == ["unknown_position"]
    assert div[0].severity == "FREEZE"


def test_t6_unknown_equity_freezes():
    st = _state([])
    bv = _view(st.cash, equity={"ABC": 100})
    div = diff_positions(bv, st)
    assert [d.kind for d in div] == ["unknown_position"]
    assert div[0].severity == "FREEZE"


def test_t7_leg_vanished_with_no_counterpart_freezes():
    """Leg absent at the broker but no shares/cash appeared: symbol change or
    delisting, the A10b class."""
    st = _state([_pos("XYZ", short=_short("XYZ", 100.0, "P", 3))])
    bv = _view(st.cash, options={}, equity={})
    div = diff_positions(bv, st)
    assert [d.kind for d in div] == ["leg_vanished"]
    assert div[0].severity == "FREEZE"


def test_position_divergence_suppresses_the_cash_row():
    """When positions diverge, cash necessarily diverges too -- reporting it
    separately is noise, and the restatement is the human's job anyway."""
    st = _state([_pos("XYZ", short=_short("XYZ", 100.0, "P", 3))])
    bv = _view(st.cash - 30_000.0, options={}, equity={"XYZ": 300})
    kinds = [d.kind for d in diff_positions(bv, st)]
    assert "cash_drift" not in kinds


def test_equity_count_mismatch_on_a_known_ticker_freezes():
    """Shares differ on a ticker the state holds, options untouched."""
    st = _state([_pos("XYZ", shares=300, phase="CALL", basis=95.0)])
    bv = _view(st.cash, equity={"XYZ": 200})
    div = diff_positions(bv, st)
    assert [d.kind for d in div] == ["equity_mismatch"]
    assert div[0].severity == "FREEZE"


def test_malformed_broker_view_dies_with_a_message():
    """Skeptic 2026-08-02: the defense seam itself must not crash with a bare
    KeyError on a bad mapper output -- it dies immediately, named."""
    st = _state([])
    with pytest.raises(ValueError, match="cash"):
        diff_positions({"options": {}, "equity": {}}, st)
    with pytest.raises(ValueError, match="expiry"):
        diff_positions({"cash": 0.0,
                        "options": {("XYZ", pd.Timestamp("2026-08-21"),
                                     100.0, "P"): -1}}, st)


def test_option_count_mismatch_fallback_freezes():
    """A shape the taxonomy can't explain (broker LONG the bot's short strike)
    must still surface, as the generic fallback."""
    st = _state([_pos("XYZ", short=_short("XYZ", 100.0, "P", 3))])
    bv = _view(st.cash, options={_key("XYZ", 100.0, "P"): 2}, equity={})
    div = diff_positions(bv, st)
    assert div and all(d.severity == "FREEZE" for d in div)
    assert div[0].kind == "option_count_mismatch"
