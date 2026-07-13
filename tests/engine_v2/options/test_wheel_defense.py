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
