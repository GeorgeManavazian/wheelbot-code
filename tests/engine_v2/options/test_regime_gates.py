"""Regime gates (macro phase 2, spec 2026-07-14): entry/roll denied in unpaid
decline, stop suppressed in stressed vol. Ticker state, strictly-prior-day,
zero new knobs. All default-off — plain behavior byte-identical."""
import pandas as pd
import pytest
from src.engine_v2.options.wheel import (WheelConfig, run_wheel,
                                         is_unpaid_decline, _state_before,
                                         GATE_STALENESS_DAYS)

COLS = ["date","expiry","dte","strike","right","bid","ask","mid","close","delta","iv","underlying"]

def _chain(rows):
    ch = pd.DataFrame(rows, columns=COLS)
    ch["date"] = pd.to_datetime(ch["date"]); ch["expiry"] = pd.to_datetime(ch["expiry"])
    return ch

def _cfg(**kw):
    base = dict(starting_capital=50_000.0, put_delta=0.30, call_delta=0.30,
                take_profit_pct=None, commission_per_contract=0.0)
    base.update(kw); return WheelConfig(**base)

def _states(rows):
    """rows: list of (date, trend, vol) — minimal regime_series stand-in."""
    df = pd.DataFrame(rows, columns=["date","trend","vol"]).set_index("date")
    df.index = pd.to_datetime(df.index)
    return df

PUT_DAY = [["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0]]

# ---- constants + helper ----

def test_is_unpaid_decline_cells():
    assert is_unpaid_decline("downtrend", "calm")
    assert is_unpaid_decline("downtrend", "normal")
    assert not is_unpaid_decline("downtrend", "stressed")
    assert not is_unpaid_decline("uptrend", "calm")
    assert not is_unpaid_decline("chop", "normal")
    assert not is_unpaid_decline("unknown", "unknown")

def test_state_before_is_strictly_prior():
    st = _states([("2024-01-01","uptrend","calm"), ("2024-01-02","downtrend","calm")])
    assert _state_before(st, pd.Timestamp("2024-01-02")) == ("uptrend", "calm")
    assert _state_before(st, pd.Timestamp("2024-01-03")) == ("downtrend", "calm")

def test_state_before_staleness_bound():
    st = _states([("2024-01-01","downtrend","calm")])
    ok = pd.Timestamp("2024-01-01") + pd.Timedelta(days=GATE_STALENESS_DAYS)
    assert _state_before(st, ok) == ("downtrend", "calm")
    assert _state_before(st, ok + pd.Timedelta(days=1)) == ("unknown", "unknown")

def test_state_before_empty_or_future_only():
    st = _states([("2024-06-01","downtrend","calm")])
    assert _state_before(st, pd.Timestamp("2024-01-02")) == ("unknown", "unknown")

# ---- validation ----

def test_gate_flag_without_states_raises():
    for flag in ("regime_entry_gate", "regime_roll_gate"):
        with pytest.raises(ValueError):
            run_wheel(_chain(PUT_DAY), _cfg(**{flag: True}))

def test_stop_gate_without_stop_raises():
    st = _states([("2024-01-01","uptrend","calm")])
    with pytest.raises(ValueError):
        run_wheel(_chain(PUT_DAY), _cfg(regime_stop_gate=True), regime_states=st)

# ---- entry gate ----

def test_entry_gate_blocks_new_campaign_in_unpaid_decline():
    st = _states([("2024-01-01","downtrend","calm")])
    res = run_wheel(_chain(PUT_DAY), _cfg(regime_entry_gate=True), regime_states=st)
    assert not [t for t in res.trades if t.action == "SELL_PUT"]
    assert res.days_entry_gated == 1
    assert (pd.Timestamp("2024-01-02"), "entry_gated", None) in res.gate_events

def test_entry_gate_allows_entry_outside_unpaid_decline():
    for trend, vol in [("uptrend","calm"), ("downtrend","stressed"), ("chop","normal")]:
        st = _states([("2024-01-01",trend,vol)])
        res = run_wheel(_chain(PUT_DAY), _cfg(regime_entry_gate=True), regime_states=st)
        assert [t for t in res.trades if t.action == "SELL_PUT"], (trend, vol)
        assert res.days_entry_gated == 0

def test_entry_gate_unknown_state_allows_and_warns():
    st = _states([("2023-06-01","downtrend","calm")])   # stale > 14d
    res = run_wheel(_chain(PUT_DAY), _cfg(regime_entry_gate=True), regime_states=st)
    assert [t for t in res.trades if t.action == "SELL_PUT"]
    assert (pd.Timestamp("2024-01-02"), "gate_state_unknown", "entry") in res.warnings

def test_entry_gate_does_not_gate_call_phase():
    # assigned shares; the call phase continues the OLD campaign, so the call
    # is written even though the state has turned unpaid-decline by then.
    rows = [
        ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-09","2024-01-09",0,470,"P",5.00,5.10,5.05,5.05,-0.99,0.1,465.0],
        ["2024-01-09","2024-01-16",7,475,"C",1.00,1.10,1.05,1.05,0.30,0.1,465.0],
    ]
    st = _states([("2024-01-01","uptrend","calm"), ("2024-01-08","downtrend","calm")])
    res = run_wheel(_chain(rows), _cfg(regime_entry_gate=True), regime_states=st)
    assert [t.action for t in res.trades] == ["SELL_PUT", "ASSIGNED", "SELL_CALL"]

def test_entry_gate_off_ignores_states_entirely():
    st = _states([("2024-01-01","downtrend","calm")])
    res = run_wheel(_chain(PUT_DAY), _cfg(), regime_states=st)
    assert [t for t in res.trades if t.action == "SELL_PUT"]
    assert res.days_entry_gated == 0 and res.gate_events == []
