"""Regime router (spec 2026-07-14-regime-router-design): uptrend->hold shares,
chop->wheel+basis, downtrend+stressed->wheel, downtrend+quiet->cash,
unknown->wheel. Approach A borders. Zero knobs, EOD, strictly-prior-day state."""
import pandas as pd
import pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel
from src.engine_v2.options.regime_router import run_regime_router

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

PUT_DAY = [["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0]]
UP = [("2024-01-01","uptrend","calm",0.2)]
CHOP = [("2024-01-01","chop","normal",0.5)]

# ---- anchor: WHEEL posture == solo wheel+basis, byte for byte ----

def test_all_chop_states_byte_identical_to_solo_wheel():
    ch = pd.read_parquet("data/options/spy_greeks_eod_all.parquet")
    dates = pd.to_datetime(ch["date"]).sort_values().unique()
    st = pd.DataFrame({"trend": "chop", "vol": "normal", "vol_pctile": 0.5},
                      index=pd.DatetimeIndex(dates) - pd.Timedelta(days=1))
    cfg = WheelConfig(ticker="SPY", put_delta=0.20, call_delta=0.20, target_dte=7,
                      take_profit_pct=0.50, starting_capital=100_000.0,
                      call_min_strike="basis")
    solo = run_wheel(ch, cfg)
    rt = run_regime_router(ch, cfg, st)
    assert [(t.date, t.action, t.contracts, t.price_per_contract, t.cash_after)
            for t in solo.trades] == \
           [(t.date, t.action, t.contracts, t.price_per_contract, t.cash_after)
            for t in rt.trades]
    assert solo.equity.equals(rt.equity) and solo.final_cash == rt.final_cash

# ---- validation ----

def test_missing_states_raises():
    with pytest.raises(ValueError):
        run_regime_router(_chain(PUT_DAY), _cfg(), None)

def test_solo_only_flags_raise():
    st = _states(CHOP)
    for kw in ({"roll_tested_puts": True}, {"put_stop_mult": 3.0},
               {"regime_entry_gate": True}, {"liquidate_assignment": True,
                                             "call_min_strike": None}):
        with pytest.raises(ValueError):
            run_regime_router(_chain(PUT_DAY), _cfg(**kw), st)

def test_unknown_state_routes_to_wheel_and_warns():
    st = _states([("2023-06-01","chop","normal",0.5)])   # stale >14d
    res = run_regime_router(_chain(PUT_DAY), _cfg(), st)
    assert [t for t in res.trades if t.action == "SELL_PUT"]
    assert any(w[1] == "route_state_unknown" for w in res.warnings)
