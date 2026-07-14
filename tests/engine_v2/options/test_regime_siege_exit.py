"""Regime siege exit (spec 2026-07-14): uncovered shares sold on unpaid-decline
days, held through panic/uptrend/chop/unknown. Zero knobs, default off."""
import pandas as pd
import pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel
from src.engine_v2.options.report import wheel_report, format_report, position_log

COLS = ["date","expiry","dte","strike","right","bid","ask","mid","close","delta","iv","underlying"]

def _chain(rows):
    ch = pd.DataFrame(rows, columns=COLS)
    ch["date"] = pd.to_datetime(ch["date"]); ch["expiry"] = pd.to_datetime(ch["expiry"])
    return ch

def _cfg(**kw):
    base = dict(starting_capital=50_000.0, put_delta=0.30, call_delta=0.30,
                take_profit_pct=None, commission_per_contract=0.0,
                call_min_strike="basis")
    base.update(kw); return WheelConfig(**base)

def _states(rows):
    df = pd.DataFrame(rows, columns=["date","trend","vol"]).set_index("date")
    df.index = pd.to_datetime(df.index)
    return df

# put 470 assigned at 465 (net basis 468 after 2.00 premium); the only call is
# 465 < floor -> uncovered day on 2024-01-09.
SIEGE = [
    ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
    ["2024-01-09","2024-01-09",0,470,"P",5.00,5.10,5.05,5.05,-0.99,0.1,465.0],
    ["2024-01-09","2024-01-16",7,465,"C",3.00,3.10,3.05,3.05,0.30,0.1,465.0],
]
UNPAID = [("2024-01-08","downtrend","calm")]

def test_siege_exit_fires_on_uncovered_unpaid_day():
    res = run_wheel(_chain(SIEGE), _cfg(regime_siege_exit=True),
                    regime_states=_states(UNPAID))
    exits = [t for t in res.trades if t.action == "SIEGE_EXIT"]
    assert len(exits) == 1
    e = exits[0]
    assert e.date == pd.Timestamp("2024-01-09")
    assert e.price_per_contract == pytest.approx(465.0)     # EOD spot
    assert res.final_shares == 0
    assert (pd.Timestamp("2024-01-09"), "siege_exit", None) in res.gate_events
    # campaign continuity: exit carries the campaign id of the assigned put
    assigned = [t for t in res.trades if t.action == "ASSIGNED"][0]
    assert e.campaign_id == assigned.campaign_id
    # cash: 50k + 200 premium - 47,000 assignment + 46,500 exit
    assert res.final_cash == pytest.approx(50_000 + 200 - 47_000 + 46_500)

def test_siege_exit_holds_outside_unpaid_decline():
    for trend, vol in [("downtrend","stressed"), ("uptrend","calm"), ("chop","normal")]:
        res = run_wheel(_chain(SIEGE), _cfg(regime_siege_exit=True),
                        regime_states=_states([("2024-01-08",trend,vol)]))
        assert not [t for t in res.trades if t.action == "SIEGE_EXIT"], (trend, vol)
        assert res.final_shares == 100
        assert res.days_shares_uncovered == 1

def test_siege_exit_holds_on_unknown_state_and_warns():
    stale = _states([("2023-06-01","downtrend","calm")])
    res = run_wheel(_chain(SIEGE), _cfg(regime_siege_exit=True), regime_states=stale)
    assert not [t for t in res.trades if t.action == "SIEGE_EXIT"]
    assert (pd.Timestamp("2024-01-09"), "gate_state_unknown", "siege") in res.warnings

def test_covered_day_never_exits():
    # call at 470 >= floor 468 -> call written, not a siege day
    rows = SIEGE[:2] + [
        ["2024-01-09","2024-01-16",7,470,"C",0.50,0.60,0.55,0.55,0.05,0.1,465.0],
    ]
    res = run_wheel(_chain(rows), _cfg(regime_siege_exit=True),
                    regime_states=_states(UNPAID))
    assert [t for t in res.trades if t.action == "SELL_CALL"]
    assert not [t for t in res.trades if t.action == "SIEGE_EXIT"]

def test_no_reentry_same_day_after_exit():
    res = run_wheel(_chain(SIEGE), _cfg(regime_siege_exit=True),
                    regime_states=_states(UNPAID))
    sells = [t for t in res.trades if t.action == "SELL_PUT"
             and pd.Timestamp(t.date) == pd.Timestamp("2024-01-09")]
    assert sells == []   # entry step already ran before the exit; next entry tomorrow

def test_strictly_prior_state_no_exit_on_same_day_flip():
    # state turns unpaid ON the uncovered day; prior day is benign -> hold
    st = _states([("2024-01-08","uptrend","calm"), ("2024-01-09","downtrend","calm")])
    res = run_wheel(_chain(SIEGE), _cfg(regime_siege_exit=True), regime_states=st)
    assert not [t for t in res.trades if t.action == "SIEGE_EXIT"]

def test_validation_errors():
    st = _states(UNPAID)
    with pytest.raises(ValueError):   # no states
        run_wheel(_chain(SIEGE), _cfg(regime_siege_exit=True))
    with pytest.raises(ValueError):   # no basis floor
        run_wheel(_chain(SIEGE), _cfg(regime_siege_exit=True, call_min_strike=None),
                  regime_states=st)
    with pytest.raises(ValueError):   # liquidate never holds shares
        run_wheel(_chain(SIEGE), _cfg(regime_siege_exit=True, call_min_strike=None,
                                      liquidate_assignment=True), regime_states=st)

def test_position_log_and_report_surface_the_exit():
    cfg = _cfg(regime_siege_exit=True)
    res = run_wheel(_chain(SIEGE), cfg, regime_states=_states(UNPAID))
    log = position_log(res, cfg)
    row = log[log.outcome == "Siege exit"]
    assert len(row) == 1 and row.iloc[0]["instrument"] == "SHARES"
    assert row.iloc[0]["realized_pnl"] == pytest.approx((465.0 - 470.0) * 100)
    rep = wheel_report(res, _chain(SIEGE), cfg)
    assert rep.gates["n_siege_exits"] == 1
    assert "siege exits" in format_report(rep)

def test_flag_off_is_inert():
    res = run_wheel(_chain(SIEGE), _cfg(), regime_states=_states(UNPAID))
    assert not [t for t in res.trades if t.action == "SIEGE_EXIT"]
    assert res.final_shares == 100
