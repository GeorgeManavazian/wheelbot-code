"""Portfolio rotation (spec 2026-07-14): one shared pool, entries routed to the
eligible ticker with the richest premium (vol pctile desc, fixed tie order).
Zero knobs, EOD v1, basis floor on, strictly-prior-day state."""
import pandas as pd
import pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel
from src.engine_v2.options.portfolio import (run_portfolio_wheel,
                                             ROTATION_TIE_ORDER)

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
    """rows: (date, trend, vol, vol_pctile)"""
    df = pd.DataFrame(rows, columns=["date","trend","vol","vol_pctile"]).set_index("date")
    df.index = pd.to_datetime(df.index)
    return df

# one clean put day per ticker, same expiry/strike shape, different underlyings
def _put_day(und, strike, bid=2.00):
    return [["2024-01-02","2024-01-09",7,strike,"P",bid,bid+0.10,bid+0.05,bid+0.05,-0.30,0.1,und]]

SPY_CH = _put_day(472.0, 470)
GDX_CH = _put_day(40.0, 39)

CALM = [("2024-01-01","uptrend","calm",0.20)]
RICH = [("2024-01-01","uptrend","stressed",0.90)]
UNPAID = [("2024-01-01","downtrend","normal",0.50)]


def test_universe_of_one_matches_solo_run_wheel():
    cfg = _cfg()
    solo = run_wheel(_chain(SPY_CH), cfg)
    port = run_portfolio_wheel({"SPY": _chain(SPY_CH)}, cfg,
                               {"SPY": _states(CALM)})
    assert [(t.date, t.action, t.contracts, t.price_per_contract, t.cash_after)
            for t in solo.trades] == \
           [(t.date, t.action, t.contracts, t.price_per_contract, t.cash_after)
            for t in port.trades]
    assert solo.equity.equals(port.equity)
    assert solo.final_cash == port.final_cash

def test_routes_to_highest_vol_pctile():
    port = run_portfolio_wheel({"SPY": _chain(SPY_CH), "GDX": _chain(GDX_CH)},
                               _cfg(),
                               {"SPY": _states(CALM), "GDX": _states(RICH)})
    entries = [t for t in port.trades if t.action == "SELL_PUT"]
    assert entries and entries[0].contract.root == "GDX"
    assert port.route_events and port.route_events[0][2] == "GDX"

def test_tie_breaks_by_fixed_order():
    port = run_portfolio_wheel({"SPY": _chain(SPY_CH), "GDX": _chain(GDX_CH)},
                               _cfg(),
                               {"SPY": _states(RICH), "GDX": _states(RICH)})
    entries = [t for t in port.trades if t.action == "SELL_PUT"]
    assert entries[0].contract.root == "SPY"   # SPY before GDX in tie order
    assert ROTATION_TIE_ORDER.index("SPY") < ROTATION_TIE_ORDER.index("GDX")

def test_unpaid_decline_ticker_is_skipped():
    port = run_portfolio_wheel({"SPY": _chain(SPY_CH), "GDX": _chain(GDX_CH)},
                               _cfg(),
                               {"SPY": _states(UNPAID), "GDX": _states(CALM)})
    entries = [t for t in port.trades if t.action == "SELL_PUT"]
    assert entries and entries[0].contract.root == "GDX"

def test_all_unpaid_means_flat_day():
    port = run_portfolio_wheel({"SPY": _chain(SPY_CH), "GDX": _chain(GDX_CH)},
                               _cfg(),
                               {"SPY": _states(UNPAID), "GDX": _states(UNPAID)})
    assert not [t for t in port.trades if t.action == "SELL_PUT"]
    assert port.days_flat == 1

def test_unknown_state_allows_and_warns():
    stale = [("2023-06-01","downtrend","normal",0.50)]   # >14d stale -> unknown
    port = run_portfolio_wheel({"SPY": _chain(SPY_CH)}, _cfg(),
                               {"SPY": _states(stale)})
    assert [t for t in port.trades if t.action == "SELL_PUT"]
    assert any(w[1] == "route_state_unknown" for w in port.warnings)

def test_clean_start_excludes_ticker():
    port = run_portfolio_wheel({"SPY": _chain(SPY_CH), "GDX": _chain(GDX_CH)},
                               _cfg(),
                               {"SPY": _states(UNPAID), "GDX": _states(RICH)},
                               clean_start={"GDX": pd.Timestamp("2025-01-01")})
    # GDX rich but before its clean start; SPY unpaid -> nobody enters
    assert not [t for t in port.trades if t.action == "SELL_PUT"]

def test_one_campaign_at_a_time():
    # both tickers eligible for many days; a put held on GDX must block any
    # SPY entry until the GDX campaign resolves.
    gdx = [
        ["2024-01-02","2024-01-09",7,39,"P",2.00,2.10,2.05,2.05,-0.30,0.1,40.0],
        ["2024-01-03","2024-01-09",6,39,"P",2.00,2.10,2.05,2.05,-0.30,0.1,40.0],
        ["2024-01-09","2024-01-09",0,39,"P",0.05,0.10,0.07,0.07,-0.01,0.1,42.0],
    ]
    spy = [
        ["2024-01-03","2024-01-10",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-09","2024-01-10",1,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-10","2024-01-10",0,470,"P",0.05,0.10,0.07,0.07,-0.01,0.1,475.0],
    ]
    port = run_portfolio_wheel({"SPY": _chain(spy), "GDX": _chain(gdx)},
                               _cfg(),
                               {"SPY": _states(CALM), "GDX": _states(RICH)})
    sells = [t for t in port.trades if t.action == "SELL_PUT"]
    roots = [t.contract.root for t in sells]
    assert roots[0] == "GDX"
    # SPY entry only AFTER the GDX put expired on 01-09
    if len(roots) > 1:
        assert sells[1].date >= pd.Timestamp("2024-01-09")

def test_no_lookahead_same_day_state_flip_ignored():
    flip = [("2024-01-01","uptrend","calm",0.20),
            ("2024-01-02","downtrend","normal",0.50)]   # flips ON entry day
    port = run_portfolio_wheel({"SPY": _chain(SPY_CH)}, _cfg(),
                               {"SPY": _states(flip)})
    assert [t for t in port.trades if t.action == "SELL_PUT"]   # prior day rules

def test_validation_missing_states_for_member():
    with pytest.raises(ValueError):
        run_portfolio_wheel({"SPY": _chain(SPY_CH), "GDX": _chain(GDX_CH)},
                            _cfg(), {"SPY": _states(CALM)})

def test_validation_refuses_solo_only_mechanics():
    for kw in ({"roll_tested_puts": True}, {"put_stop_mult": 3.0},
               {"regime_entry_gate": True}):
        with pytest.raises(ValueError):
            run_portfolio_wheel({"SPY": _chain(SPY_CH)}, _cfg(**kw),
                                {"SPY": _states(CALM)})
