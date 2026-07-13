"""Defense variants (spec amendment 2026-07-12b): no-calls-below-basis,
roll-puts, liquidate-at-assignment, put-stop. All default-off — plain
behavior must be byte-identical."""
import pandas as pd
import pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel

COLS = ["date","expiry","dte","strike","right","bid","ask","mid","close","delta","iv","underlying"]

def _chain(rows):
    ch = pd.DataFrame(rows, columns=COLS)
    ch["date"] = pd.to_datetime(ch["date"]); ch["expiry"] = pd.to_datetime(ch["expiry"])
    return ch

def _cfg(**kw):
    base = dict(starting_capital=50_000.0, put_delta=0.30, call_delta=0.30,
                take_profit_pct=None, commission_per_contract=0.0)
    base.update(kw); return WheelConfig(**base)

# assignment setup shared by the call_min_strike tests: put 470 assigned at
# spot 465, then call strikes on the assignment day.
_ASSIGN = [
    ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
    ["2024-01-09","2024-01-09",0,470,"P",5.00,5.10,5.05,5.05,-0.99,0.1,465.0],
]

# ---- variant: no-calls-below-basis (call_min_strike="basis") ----

def test_call_min_strike_basis_restricts_to_strikes_at_or_above_basis():
    # nearest-delta call is 465 (below the 470 basis); only the tiny-delta 470
    # and 475 sit at/above basis -> must pick from those, nearest target delta.
    rows = _ASSIGN + [
        ["2024-01-09","2024-01-16",7,465,"C",3.00,3.10,3.05,3.05,0.30,0.1,465.0],
        ["2024-01-09","2024-01-16",7,470,"C",0.50,0.60,0.55,0.55,0.05,0.1,465.0],
        ["2024-01-09","2024-01-16",7,475,"C",0.20,0.30,0.25,0.25,0.02,0.1,465.0],
    ]
    res = run_wheel(_chain(rows), _cfg(call_min_strike="basis"))
    calls = [t for t in res.trades if t.action == "SELL_CALL"]
    assert calls and calls[0].contract.strike >= 470.0
    assert calls[0].contract.strike == 470.0     # 0.05 delta beats 0.02 for 0.30 target

def test_call_min_strike_none_keeps_plain_behavior():
    rows = _ASSIGN + [
        ["2024-01-09","2024-01-16",7,465,"C",3.00,3.10,3.05,3.05,0.30,0.1,465.0],
        ["2024-01-09","2024-01-16",7,470,"C",0.50,0.60,0.55,0.55,0.05,0.1,465.0],
    ]
    res = run_wheel(_chain(rows), _cfg())     # default: no basis floor
    calls = [t for t in res.trades if t.action == "SELL_CALL"]
    assert calls and calls[0].contract.strike == 465.0

def test_call_min_strike_basis_no_eligible_strike_sells_nothing_holds_shares():
    # only strikes below the 470 basis exist in the selected expiry -> no call
    # that day; shares held (exposed, so NOT counted flat).
    rows = _ASSIGN + [
        ["2024-01-09","2024-01-16",7,460,"C",4.00,4.10,4.05,4.05,0.45,0.1,465.0],
        ["2024-01-09","2024-01-16",7,465,"C",3.00,3.10,3.05,3.05,0.30,0.1,465.0],
    ]
    res = run_wheel(_chain(rows), _cfg(call_min_strike="basis"))
    assert not [t for t in res.trades if t.action == "SELL_CALL"]
    assert res.final_shares == 100
    assert res.days_flat == 0    # holding shares is exposed, not flat

def test_basis_clears_after_called_away():
    # assigned @470, called away @475, re-assigned @450 -> new basis is 450,
    # so a 455 call (>= 450, < 470) is legal.
    rows = [
        ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-09","2024-01-09",0,470,"P",5.00,5.10,5.05,5.05,-0.99,0.1,465.0],
        ["2024-01-09","2024-01-16",7,475,"C",3.00,3.10,3.05,3.05,0.30,0.1,465.0],
        ["2024-01-16","2024-01-16",0,475,"C",5.00,5.10,5.05,5.05,0.99,0.1,480.0],
        ["2024-01-16","2024-01-23",7,450,"P",2.00,2.10,2.05,2.05,-0.30,0.1,480.0],
        ["2024-01-23","2024-01-23",0,450,"P",5.00,5.10,5.05,5.05,-0.99,0.1,445.0],
        ["2024-01-23","2024-01-30",7,455,"C",2.00,2.10,2.05,2.05,0.30,0.1,445.0],
    ]
    res = run_wheel(_chain(rows), _cfg(call_min_strike="basis"))
    calls = [t for t in res.trades if t.action == "SELL_CALL"]
    assert [c.contract.strike for c in calls] == [475.0, 455.0]

# ---- repair pass: config surface (the at-expiry roll_puts variant is retired —
# it was economically dominated by liquidate_assignment; the mid-life
# roll_tested_puts replaces it, spec 2026-07-13) ----

def test_roll_puts_flag_no_longer_exists():
    with pytest.raises(TypeError):
        WheelConfig(roll_puts=True)

def test_contradictory_liquidate_plus_basis_rejected():
    rows = _ASSIGN
    with pytest.raises(ValueError):
        run_wheel(_chain(rows), _cfg(liquidate_assignment=True, call_min_strike="basis"))

def test_roll_tested_puts_flag_accepted_default_off():
    cfg = _cfg()
    assert cfg.roll_tested_puts is False
    cfg2 = _cfg(roll_tested_puts=True)
    assert cfg2.roll_tested_puts is True

# ---- variant: liquidate-at-assignment (liquidate_assignment=True) ----

def test_liquidate_assignment_dumps_shares_at_spot_same_day():
    # assignment fires, shares sold at that day's spot immediately, back to
    # PUT phase; a writable call exists but must never be sold; next entry is
    # the fresh put, same day.
    rows = [
        ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-09","2024-01-09",0,470,"P",5.00,5.10,5.05,5.05,-0.99,0.1,465.0],
        ["2024-01-09","2024-01-16",7,465,"C",3.00,3.10,3.05,3.05, 0.30,0.1,465.0],
        ["2024-01-09","2024-01-16",7,460,"P",2.00,2.10,2.05,2.05,-0.30,0.1,465.0],
    ]
    res = run_wheel(_chain(rows), _cfg(liquidate_assignment=True))
    acts = [t.action for t in res.trades]
    assert "SELL_CALL" not in acts
    assert acts == ["SELL_PUT","ASSIGNED","LIQUIDATE","SELL_PUT"]
    liq = [t for t in res.trades if t.action == "LIQUIDATE"][0]
    assert liq.price_per_contract == pytest.approx(465.0)   # that day's spot
    assert liq.date == pd.Timestamp("2024-01-09")
    fresh = [t for t in res.trades if t.action == "SELL_PUT"][1]
    assert fresh.contract.right == "P" and fresh.date == pd.Timestamp("2024-01-09")
    assert res.final_shares == 0
    # +200 credit -47000 assigned +46500 liquidate +200 credit -205 settle at mid
    assert res.final_cash == pytest.approx(50_000 + 200 - 47_000 + 46_500 + 200 - 205)

def test_liquidate_assignment_off_keeps_shares():
    rows = [
        ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-09","2024-01-09",0,470,"P",5.00,5.10,5.05,5.05,-0.99,0.1,465.0],
    ]
    res = run_wheel(_chain(rows), _cfg())
    assert [t.action for t in res.trades] == ["SELL_PUT","ASSIGNED"]
    assert res.final_shares == 100

# ---- variant: put-stop (put_stop_mult) ----

# put sold for 2.00 credit; next day its ask has tripled (6.30 >= 3 x 2.00).
_STOP = [
    ["2024-01-02","2024-01-19",17,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
    ["2024-01-03","2024-01-19",16,470,"P",6.20,6.30,6.25,6.25,-0.60,0.1,460.0],
]

def test_put_stop_fires_at_ask_when_mark_triples():
    # TP is on (threshold 1.00, not hit) — the stop runs AFTER the TP check.
    res = run_wheel(_chain(_STOP), _cfg(take_profit_pct=0.50, target_dte=17,
                                        put_stop_mult=3.0))
    acts = [t.action for t in res.trades]
    assert acts == ["SELL_PUT","STOP_CLOSE"]
    stop = [t for t in res.trades if t.action == "STOP_CLOSE"][0]
    assert stop.price_per_contract == pytest.approx(6.30)   # that day's ask
    assert res.final_cash == pytest.approx(50_000 + 200 - 630)

def test_put_stop_none_changes_nothing():
    res = run_wheel(_chain(_STOP), _cfg(take_profit_pct=0.50, target_dte=17))
    acts = [t.action for t in res.trades]
    assert acts == ["SELL_PUT"]                # held; settled at mark at the end
    assert res.final_cash == pytest.approx(50_000 + 200 - 625)   # mid 6.25

def test_put_stop_does_not_fire_below_threshold():
    rows = [
        ["2024-01-02","2024-01-19",17,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-03","2024-01-19",16,470,"P",5.80,5.90,5.85,5.85,-0.55,0.1,461.0],
    ]
    res = run_wheel(_chain(rows), _cfg(take_profit_pct=0.50, target_dte=17,
                                       put_stop_mult=3.0))
    assert [t.action for t in res.trades] == ["SELL_PUT"]   # 5.90 < 6.00

def test_put_stop_applies_to_puts_only():
    # short CALL's ask triples -> no stop (spec: calls are not the losing leg)
    rows = [
        ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-09","2024-01-09",0,470,"P",5.00,5.10,5.05,5.05,-0.99,0.1,465.0],
        ["2024-01-09","2024-01-16",7,465,"C",3.00,3.10,3.05,3.05, 0.30,0.1,465.0],
        ["2024-01-10","2024-01-16",6,465,"C",14.90,15.10,15.00,15.00,0.99,0.1,480.0],
    ]
    res = run_wheel(_chain(rows), _cfg(put_stop_mult=3.0))
    assert "STOP_CLOSE" not in [t.action for t in res.trades]

# ---- repair pass: stop fixes ----

def test_put_stop_cannot_fire_on_expiry_day():
    # expiry day, deep ITM, ask >= 3x credit: stop must NOT fire; assignment
    # (intrinsic, no spread) resolves the day instead.
    rows = [
        ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-09","2024-01-09",0,470,"P",9.00,9.10,9.05,9.05,-0.99,0.1,461.0],
    ]
    res = run_wheel(_chain(rows), _cfg(put_stop_mult=3.0))
    acts = [t.action for t in res.trades]
    assert "STOP_CLOSE" not in acts
    assert "ASSIGNED" in acts

def test_stop_blocks_all_entries_same_day():
    # stop fires mid-life; a fresh sellable put exists the same day at another
    # strike -> entry must NOT happen (stop means flat), but next day it may.
    rows = [
        ["2024-01-02","2024-01-16",14,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-05","2024-01-16",11,470,"P",6.50,6.60,6.55,6.55,-0.70,0.1,463.0],
        # band-eligible same-day candidate (dte 14): must NOT be sold (stop = flat)
        ["2024-01-05","2024-01-19",14,455,"P",2.00,2.10,2.05,2.05,-0.30,0.1,463.0],
        # band-eligible next-day candidate: normal entry resumes
        ["2024-01-08","2024-01-22",14,455,"P",2.00,2.10,2.05,2.05,-0.30,0.1,463.0],
    ]
    res = run_wheel(_chain(rows), _cfg(put_stop_mult=3.0, target_dte=14))
    stop_day = [t for t in res.trades if t.action == "STOP_CLOSE"][0].date
    same_day_entries = [t for t in res.trades
                        if t.action == "SELL_PUT" and t.date == stop_day]
    assert same_day_entries == []
    next_day_entries = [t for t in res.trades
                        if t.action == "SELL_PUT" and t.date > stop_day]
    assert len(next_day_entries) == 1

def test_stop_check_missing_mark_logs_warning():
    # day 2 has no row for the held contract at all -> stop check skipped + logged
    rows = [
        ["2024-01-02","2024-01-16",14,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-05","2024-01-16",11,455,"P",2.00,2.10,2.05,2.05,-0.30,0.1,463.0],
    ]
    res = run_wheel(_chain(rows), _cfg(put_stop_mult=3.0, target_dte=14))
    assert any(w[1] == "stop_check_no_mark" for w in res.warnings)

# ---- repair pass: mid-life roll ----

# tested put mid-life: 470P sold at 472, spot drops to 468 (tested) with days
# left; a farther-dated 460P pays more than the buyback -> credit-only roll fires.
_ROLLABLE = [
    ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
    ["2024-01-04","2024-01-09",5,470,"P",3.00,3.10,3.05,3.05,-0.55,0.1,468.0],
    ["2024-01-04","2024-01-11",7,460,"P",3.20,3.30,3.25,3.25,-0.30,0.1,468.0],
]

def test_tested_put_rolls_midlife_with_credit():
    res = run_wheel(_chain(_ROLLABLE), _cfg(roll_tested_puts=True))
    acts = [t.action for t in res.trades]
    assert acts == ["SELL_PUT", "ROLL_CLOSE", "ROLL_OPEN"]
    rc = res.trades[1]; ro = res.trades[2]
    assert rc.price_per_contract == pytest.approx(3.10)   # buyback at ask
    assert ro.price_per_contract == pytest.approx(3.20)   # new leg at bid
    assert ro.contract.strike == 460.0
    assert rc.campaign_id == ro.campaign_id == 1          # same saga

def test_roll_requires_net_credit():
    # new leg bid (2.90) < buyback ask (3.10) -> net debit -> no roll
    rows = [_ROLLABLE[0], _ROLLABLE[1],
        ["2024-01-04","2024-01-11",7,460,"P",2.90,3.00,2.95,2.95,-0.30,0.1,468.0]]
    res = run_wheel(_chain(rows), _cfg(roll_tested_puts=True))
    assert [t.action for t in res.trades] == ["SELL_PUT"]

def test_roll_not_triggered_when_not_tested():
    # spot 471 > strike 470 -> not tested -> no roll even though credit exists
    rows = [_ROLLABLE[0],
        ["2024-01-04","2024-01-09",5,470,"P",3.00,3.10,3.05,3.05,-0.45,0.1,471.0],
        ["2024-01-04","2024-01-11",7,460,"P",3.20,3.30,3.25,3.25,-0.30,0.1,471.0]]
    res = run_wheel(_chain(rows), _cfg(roll_tested_puts=True))
    assert [t.action for t in res.trades] == ["SELL_PUT"]

def test_roll_cap_two_then_normal_expiry_path():
    # three tested days each offering a credit roll -> only 2 rolls fire; the
    # third leg runs to expiry and is assigned.
    rows = [
        ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        # day 2: tested, roll 1 -> 460P Jan-10 (dte 7, in band)
        ["2024-01-03","2024-01-09",6,470,"P",3.00,3.10,3.05,3.05,-0.55,0.1,468.0],
        ["2024-01-03","2024-01-10",7,460,"P",3.20,3.30,3.25,3.25,-0.30,0.1,468.0],
        # day 3: tested again, roll 2 -> 450P Jan-11 (dte 7, in band)
        ["2024-01-04","2024-01-10",6,460,"P",3.00,3.10,3.05,3.05,-0.55,0.1,458.0],
        ["2024-01-04","2024-01-11",7,450,"P",3.20,3.30,3.25,3.25,-0.30,0.1,458.0],
        # day 4: tested a third time, in-band credit roll available - cap says NO
        ["2024-01-05","2024-01-11",6,450,"P",3.00,3.10,3.05,3.05,-0.55,0.1,448.0],
        ["2024-01-05","2024-01-12",7,440,"P",3.20,3.30,3.25,3.25,-0.30,0.1,448.0],
        # expiry of the second rolled leg: ITM -> assigned
        ["2024-01-11","2024-01-11",0,450,"P",5.00,5.10,5.05,5.05,-0.99,0.1,445.0],
    ]
    res = run_wheel(_chain(rows), _cfg(roll_tested_puts=True, target_dte=7))
    acts = [t.action for t in res.trades]
    assert acts.count("ROLL_CLOSE") == 2
    assert acts[-1] == "ASSIGNED"

def test_roll_beats_stop_when_both_would_fire():
    # ask 6.60 >= 3x credit 2.00 AND tested AND credit roll available -> roll
    # only; the stop is skipped for the day (spec: stop re-evaluates against the
    # new leg from the next day).
    rows = [_ROLLABLE[0],
        ["2024-01-04","2024-01-09",5,470,"P",6.50,6.60,6.55,6.55,-0.80,0.1,464.0],
        ["2024-01-04","2024-01-11",7,455,"P",6.70,6.80,6.75,6.75,-0.30,0.1,464.0]]
    res = run_wheel(_chain(rows), _cfg(roll_tested_puts=True, put_stop_mult=3.0))
    acts = [t.action for t in res.trades]
    assert "ROLL_CLOSE" in acts and "STOP_CLOSE" not in acts

def test_roll_missing_new_leg_waits():
    # tested but no alternative expiry exists -> select returns the same
    # contract -> no roll, position simply continues.
    rows = [_ROLLABLE[0], _ROLLABLE[1]]
    res = run_wheel(_chain(rows), _cfg(roll_tested_puts=True))
    assert [t.action for t in res.trades] == ["SELL_PUT"]

def test_roll_missing_current_mark_logs_warning():
    # held contract has no row on the tested day (unknowable ask) -> warning.
    # The 455P row establishes spot 468 for the day.
    rows = [_ROLLABLE[0],
        ["2024-01-04","2024-01-11",7,455,"P",3.20,3.30,3.25,3.25,-0.30,0.1,468.0]]
    res = run_wheel(_chain(rows), _cfg(roll_tested_puts=True))
    assert any(w[1] == "roll_check_no_mark" for w in res.warnings)
