import os
import pandas as pd
import pytest
from src.engine_v2.options.select import (
    select_contract, derived_band, option_mark, intrinsic_value, expiry_underlying)
from src.engine_v2.options.chain import Contract

# NOTE: the plan named fixtures/spy_options_small.parquet, but that fixture only
# carries DTE 0-1 expiries and derived_band's floor clamps at 5, so no target_dte
# can ever select from it. spy_wheel_cycle.parquet (also a plan fixture) has a
# 42-DTE expiry on its first date -> target_dte=40 (band 38..43) is satisfiable.
FIX = "fixtures/spy_wheel_cycle.parquet"
pytestmark = pytest.mark.skipif(not os.path.exists(FIX), reason="fixture not built")

def _chain():
    return pd.read_parquet(FIX)

def test_intrinsic_value():
    assert intrinsic_value("P", 100, 90) == 10
    assert intrinsic_value("P", 100, 110) == 0
    assert intrinsic_value("C", 100, 110) == 10
    assert intrinsic_value("C", 100, 90) == 0

def test_derived_band():
    assert derived_band(7) == (5, 10)
    assert derived_band(30) == (28, 33)
    assert derived_band(5) == (5, 8)      # floor clamps at 5

def test_select_contract_expiry_first_then_strike():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    c = select_contract(ch, d, "P", 0.30, target_dte=40, root="SPY")
    assert c is not None and c.right == "P" and c.root == "SPY"
    lo, hi = derived_band(40)
    day = ch[(ch["date"] == d) & (ch["right"] == "P")]
    dtes = day.groupby("expiry")["dte"].first()
    eligible = dtes[(dtes >= lo) & (dtes <= hi)]
    # 1) chosen expiry is the eligible one nearest 40
    err = (eligible - 40).abs()
    assert (pd.Timestamp(c.expiry) in eligible[err == err.min()].index)
    # 2) within that expiry, strike is nearest-delta
    e = day[day["expiry"] == c.expiry]
    best = (e["delta"].abs() - 0.30).abs().min()
    got = (e[e["strike"] == c.strike]["delta"].abs() - 0.30).abs().iloc[0]
    assert got == pytest.approx(best, abs=1e-9)

def test_select_contract_deterministic():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    a = select_contract(ch, d, "P", 0.30, target_dte=40, root="SPY")
    b = select_contract(ch, d, "P", 0.30, target_dte=40, root="SPY")
    assert a == b

def test_select_returns_none_when_band_misses():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    # candidates exist on the date, but no expiry falls in the derived band
    assert select_contract(ch, d, "P", 0.30, target_dte=9998, root="SPY") is None

def test_select_contract_sits_out_when_band_empty():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    # target so small the band [5,8] may or may not exist in fixture; force miss:
    ch2 = ch[ch["dte"] > 200]
    assert select_contract(ch2, d, "P", 0.30, target_dte=7, root="SPY") is None

def test_select_contract_root_propagates():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    c = select_contract(ch, d, "P", 0.30, target_dte=40, root="GDX")
    assert c.root == "GDX"

def test_option_mark_and_absent():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    c = select_contract(ch, d, "P", 0.30, target_dte=40, root="SPY")
    m = option_mark(ch, d, c)
    assert m is not None and m.mid == pytest.approx((m.bid + m.ask) / 2)
    absent = Contract("SPY", c.expiry, 1.0, "P")
    assert option_mark(ch, d, absent) is None

def test_expiry_underlying():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    c = select_contract(ch, d, "P", 0.30, target_dte=40, root="SPY")
    u = expiry_underlying(ch, c)
    # the fixture includes the contract's expiry date (2024-02-16), so assert the real value
    assert u is not None
    assert u == pytest.approx(499.51, abs=0.01)
