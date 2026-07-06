import numpy as np
import pandas as pd
import pytest

from src.engine import metrics


def make_equity(values, start="2020-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)),
                     dtype=float)


def test_cagr_doubling_in_252_days():
    eq = make_equity(np.linspace(100, 200, 253))
    # 252 daily periods -> exactly one 252-day year -> CAGR 100%
    assert metrics.cagr(eq) == pytest.approx(1.0, abs=0.01)


def test_max_drawdown():
    eq = make_equity([100, 120, 90, 110])
    assert metrics.max_drawdown(eq) == pytest.approx(90 / 120 - 1)  # -25%


def test_sharpe_zero_vol_is_nan():
    eq = make_equity([100.0] * 10)
    assert np.isnan(metrics.sharpe(eq))


def test_yearly_returns():
    idx = pd.to_datetime(["2020-06-01", "2020-12-31", "2021-06-01", "2021-12-31"])
    eq = pd.Series([100.0, 110.0, 115.5, 121.0], index=idx)
    yr = metrics.yearly_returns(eq)
    assert yr.loc[2020] == pytest.approx(0.10)
    assert yr.loc[2021] == pytest.approx(0.10)  # 110 -> 121


def test_annual_turnover():
    eq = make_equity([10_000.0] * 253)  # one 252-day year, avg equity 10k
    trades = pd.DataFrame([
        {"date": eq.index[10], "ticker": "A", "side": "buy", "shares": 50, "price": 100.0},
        {"date": eq.index[100], "ticker": "A", "side": "sell", "shares": 50, "price": 100.0},
    ])
    # traded value 10k over 1 year on 10k equity -> turnover 1.0x
    assert metrics.annual_turnover(trades, eq) == pytest.approx(1.0, abs=0.01)


def test_summarize_flags_small_sample():
    eq = make_equity(np.linspace(100, 110, 50))
    holdings = eq * 0.5
    trades = pd.DataFrame([{"date": eq.index[1], "ticker": "A", "side": "buy",
                            "shares": 1, "price": 100.0}] * 5)
    class R: pass
    r = R(); r.equity, r.holdings_value, r.trades = eq, holdings, trades
    s = metrics.summarize(r)
    assert s["n_trades"] == 5
    assert s["sample_flag"] == "INSUFFICIENT <30"
    assert s["exposure"] == pytest.approx(0.5)
