import os
import pandas as pd
import pytest
from src.engine_v2.options.select import (
    select_strike_by_delta, option_mark, intrinsic_value, expiry_underlying)
from src.engine_v2.options.chain import Contract

FIX = "fixtures/spy_options_small.parquet"
pytestmark = pytest.mark.skipif(not os.path.exists(FIX), reason="fixture not built")

def _chain():
    return pd.read_parquet(FIX)

def test_intrinsic_value():
    assert intrinsic_value("P", 100, 90) == 10
    assert intrinsic_value("P", 100, 110) == 0
    assert intrinsic_value("C", 100, 110) == 10
    assert intrinsic_value("C", 100, 90) == 0

def test_select_30_delta_put():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    c = select_strike_by_delta(ch, d, "P", 0.30, dte_min=1, dte_max=60)
    assert c is not None and c.right == "P"
    cand = ch[(ch["date"] == d) & (ch["right"] == "P")
              & (ch["dte"] >= 1) & (ch["dte"] <= 60)]
    best = (cand["delta"].abs() - 0.30).abs().min()
    sel = ch[(ch["date"] == d) & (ch["expiry"] == c.expiry)
             & (ch["strike"] == c.strike) & (ch["right"] == "P")]
    got = abs(float(sel.iloc[0]["delta"]))
    assert abs(got - 0.30) == pytest.approx(best, abs=1e-9)

def test_select_returns_none_when_empty():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    assert select_strike_by_delta(ch, d, "P", 0.30, dte_min=9998, dte_max=9999) is None

def test_option_mark_and_absent():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    c = select_strike_by_delta(ch, d, "P", 0.30, dte_min=1, dte_max=60)
    m = option_mark(ch, d, c)
    assert m is not None and m.mid == pytest.approx((m.bid + m.ask) / 2)
    absent = Contract("SPY", c.expiry, 1.0, "P")
    assert option_mark(ch, d, absent) is None

def test_expiry_underlying():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    c = select_strike_by_delta(ch, d, "P", 0.30, dte_min=1, dte_max=60)
    u = expiry_underlying(ch, c)
    # underlying on the expiry date if present in the fixture, else None — both valid
    assert (u is None) or (u > 0)
