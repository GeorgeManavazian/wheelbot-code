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

# ---- variant: roll-puts (roll_puts=True) ----

def test_roll_puts_buys_back_at_ask_and_sells_fresh_put_same_day():
    # put 470 ITM at expiry (spot 465): buy back at the ask, never assign,
    # then normal entry logic sells the fresh 460 put the SAME day.
    rows = [
        ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-09","2024-01-09",0,470,"P",5.00,5.10,5.05,5.05,-0.99,0.1,465.0],
        ["2024-01-09","2024-01-16",7,460,"P",2.00,2.10,2.05,2.05,-0.30,0.1,465.0],
    ]
    res = run_wheel(_chain(rows), _cfg(roll_puts=True))
    acts = [t.action for t in res.trades]
    assert "ASSIGNED" not in acts
    assert acts == ["SELL_PUT","ROLL_CLOSE","SELL_PUT"]
    roll = [t for t in res.trades if t.action == "ROLL_CLOSE"][0]
    assert roll.price_per_contract == pytest.approx(5.10)     # that day's ask
    fresh = [t for t in res.trades if t.action == "SELL_PUT"][1]
    assert fresh.contract.strike == 460.0 and fresh.date == pd.Timestamp("2024-01-09")
    # +200 credit -510 buyback +200 credit -205 settle fresh put at mid
    assert res.final_cash == pytest.approx(50_000 + 200 - 510 + 200 - 205)
    assert res.final_shares == 0

def test_roll_puts_missing_mark_falls_back_to_intrinsic():
    # no row for the expiring 470 put on expiry day -> close at intrinsic 470-465=5.
    rows = [
        ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-09","2024-01-16",7,460,"P",2.00,2.10,2.05,2.05,-0.30,0.1,465.0],
    ]
    res = run_wheel(_chain(rows), _cfg(roll_puts=True))
    acts = [t.action for t in res.trades]
    assert "ASSIGNED" not in acts
    roll = [t for t in res.trades if t.action == "ROLL_CLOSE"][0]
    assert roll.price_per_contract == pytest.approx(5.00)     # intrinsic fallback

def test_roll_puts_skips_entry_when_only_identical_contract_available():
    # nothing sellable after the buyback except the contract just closed ->
    # no re-entry (and dte-0 is outside the band anyway); day ends flat.
    rows = [
        ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-09","2024-01-09",0,470,"P",5.00,5.10,5.05,5.05,-0.99,0.1,465.0],
    ]
    res = run_wheel(_chain(rows), _cfg(roll_puts=True))
    acts = [t.action for t in res.trades]
    assert acts == ["SELL_PUT","ROLL_CLOSE"]
    assert res.final_cash == pytest.approx(50_000 + 200 - 510)

def test_roll_puts_otm_expiry_unchanged():
    rows = [
        ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-09","2024-01-09",0,470,"P",0.00,0.05,0.02,0.02,-0.01,0.1,475.0],
    ]
    res = run_wheel(_chain(rows), _cfg(roll_puts=True))
    acts = [t.action for t in res.trades]
    assert acts == ["SELL_PUT","PUT_EXPIRED"]

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
