import pandas as pd
import numpy as np
from src.engine_v2.options.regime_router import run_regime_router
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.portfolio import DEFAULT_CLEAN_START
from src.engine_v2.options.intraday import intraday_marks
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0,
            call_min_strike="basis")


def _load(t):
    ch = pd.read_parquet(chain_path(t))
    ch["date"] = pd.to_datetime(ch["date"])
    if t in DEFAULT_CLEAN_START:
        ch = ch[ch["date"] >= DEFAULT_CLEAN_START[t]].reset_index(drop=True)
    return ch, regime_series(closes_for(t))


def test_intraday_none_is_byte_identical():
    # The load-bearing invariant: intraday=None must equal the current router
    # exactly, on every seen ticker. Protects the deep-audit-clean status.
    for t in ["SPY", "GDX", "SLV", "XOP"]:
        ch, states = _load(t)
        cfg = WheelConfig(ticker=t, **BASE)
        a = run_regime_router(ch, cfg, states)
        b = run_regime_router(ch, cfg, states, intraday=None)
        assert a.equity.equals(b.equity), t
        assert len(a.trades) == len(b.trades), t
        for ta, tb in zip(a.trades, b.trades):
            assert ta.action == tb.action and ta.date == tb.date, t
            assert ta.price_per_contract == tb.price_per_contract, t
        assert a.intraday_tp_fills == 0 and a.eod_tp_fills >= 0


def test_intraday_tp_fills_at_next_valid_bar():
    # A synthetic short whose price crosses the TP threshold mid-day fills at
    # the NEXT valid bar's close, not the crossing bar, and is booked intraday.
    ch, states = _load("SPY")
    cfg = WheelConfig(ticker="SPY", **BASE)
    base = run_regime_router(ch, cfg, states)
    # find a SELL_PUT leg that survived to EOD-TP or expiry in the base run
    sells = [t for t in base.trades if t.action == "SELL_PUT"]
    assert sells, "expected at least one SELL_PUT in the SPY router run"
    leg = sells[0]
    c = leg.contract
    credit = leg.price_per_contract
    thresh = (1 - cfg.take_profit_pct) * credit
    open_day = pd.Timestamp(leg.date).normalize()
    # one intraday day AFTER open: a crossing bar then a higher valid next bar
    day = open_day + pd.Timedelta(days=1)
    intr = {(pd.Timestamp(c.expiry), float(c.strike), c.right): pd.DataFrame({
        "timestamp": [day + pd.Timedelta(hours=h) for h in (10, 11, 12)],
        "close":     [thresh * 0.9, thresh * 0.5, credit],  # bar1 crosses, fill at bar2
    })}
    res = run_regime_router(ch, cfg, states, intraday=intr)
    fills = [t for t in res.trades
             if t.action == "CLOSE_PUT" and t.contract == c
             and pd.Timestamp(t.date) == day + pd.Timedelta(hours=11)]
    assert fills, "expected an intraday CLOSE_PUT at the next valid bar (11:00)"
    assert abs(fills[0].price_per_contract - thresh * 0.5) < 1e-9
    assert res.intraday_tp_fills >= 1


def test_last_bar_cross_falls_through_to_eod():
    # A cross on the day's LAST bar has no next bar -> no intraday fill.
    ch, states = _load("SPY")
    cfg = WheelConfig(ticker="SPY", **BASE)
    base = run_regime_router(ch, cfg, states)
    leg = [t for t in base.trades if t.action == "SELL_PUT"][0]
    c = leg.contract
    thresh = (1 - cfg.take_profit_pct) * leg.price_per_contract
    day = pd.Timestamp(leg.date).normalize() + pd.Timedelta(days=1)
    intr = {(pd.Timestamp(c.expiry), float(c.strike), c.right): pd.DataFrame({
        "timestamp": [day + pd.Timedelta(hours=10), day + pd.Timedelta(hours=11)],
        "close":     [thresh + 1.0, thresh * 0.5],  # cross only on the LAST bar
    })}
    res = run_regime_router(ch, cfg, states, intraday=intr)
    assert not [t for t in res.trades if t.action == "CLOSE_PUT"
                and t.contract == c
                and pd.Timestamp(t.date) == day + pd.Timedelta(hours=11)]


def test_zero_close_bar_never_triggers():
    # A zero-close bar below threshold is not a price and must not fill.
    ch, states = _load("SPY")
    cfg = WheelConfig(ticker="SPY", **BASE)
    base = run_regime_router(ch, cfg, states)
    leg = [t for t in base.trades if t.action == "SELL_PUT"][0]
    c = leg.contract
    thresh = (1 - cfg.take_profit_pct) * leg.price_per_contract
    day = pd.Timestamp(leg.date).normalize() + pd.Timedelta(days=1)
    intr = {(pd.Timestamp(c.expiry), float(c.strike), c.right): pd.DataFrame({
        "timestamp": [day + pd.Timedelta(hours=h) for h in (10, 11, 12)],
        "close":     [0.0, 0.0, 0.0],  # phantom bars: below thresh but invalid
    })}
    res = run_regime_router(ch, cfg, states, intraday=intr)
    assert not [t for t in res.trades if t.action == "CLOSE_PUT"
                and t.contract == c and pd.Timestamp(t.date).normalize() == day]


def test_intraday_tp_close_then_trend_entry_preserves_whipsaw_index():
    # Regression (inner-loop variable shadow): the transplanted TP loop iterates
    # over intraday bars; it MUST NOT clobber the outer enumerate index `i` that
    # step-3 records as trend_buy_idx. Stage a WHEEL put that TP-closes intraday
    # on a TREND-cell day, opening a fresh TREND position the SAME day at a large
    # date index; a downtrend flip within 10 days must register exactly one
    # whipsaw pair. Under the shadow bug, trend_buy_idx is set to the (small)
    # inner bar index, so (i - trend_buy_idx) <= 10 fails and the whipsaw is
    # silently dropped -> whipsaw_pairs == 0 instead of 1.
    dates = pd.bdate_range("2022-01-03", periods=13)  # date indices 0..12
    put_expiry = dates[10] + pd.Timedelta(days=7)     # dte 7 on the open day
    strike, spot = 49.0, 50.0
    rows = []
    for d in dates:
        # one out-of-band filler CALL per day: establishes underlying + the date,
        # never selected (dte 60 is outside the 7-DTE derived band, and puts are
        # what the WHEEL entry selects).
        rows.append(dict(date=d, expiry=d + pd.Timedelta(days=60), dte=60,
                         strike=55.0, right="C", bid=0.50, ask=0.60, mid=0.55,
                         close=0.55, delta=0.10, iv=0.2, underlying=spot))
    # the single in-band PUT, visible only on the open day (idx 10)
    rows.append(dict(date=dates[10], expiry=put_expiry, dte=7, strike=strike,
                     right="P", bid=1.00, ask=1.10, mid=1.05, close=1.05,
                     delta=-0.20, iv=0.2, underlying=spot))
    ch = pd.DataFrame(rows)
    ch["date"] = pd.to_datetime(ch["date"])
    ch["expiry"] = pd.to_datetime(ch["expiry"])

    # injected regime states (bypass warmup): cell on day d reads the row
    # STRICTLY before d. idx8->CASH day9, idx9(chop)->WHEEL day10 (SELL_PUT),
    # idx10(uptrend)->TREND day11 (intraday close + BUY_SHARES), idx11(downtrend)
    # ->forced SELL_SHARES day12 (whipsaw check).
    trend = (["downtrend"] * 9) + ["chop", "uptrend", "downtrend"]
    vol = (["calm"] * 9) + ["normal", "normal", "calm"]
    states = pd.DataFrame({"trend": trend, "vol": vol}, index=dates[:12])

    cfg = WheelConfig(ticker="SPY", **BASE)
    credit = 1.00
    thresh = (1 - cfg.take_profit_pct) * credit  # 0.50
    # bar0 crosses (index 0), fill at bar1 -> if the loop var leaked, the outer
    # index would be corrupted to 0.
    intr = {(pd.Timestamp(put_expiry), strike, "P"): pd.DataFrame({
        "timestamp": [dates[11] + pd.Timedelta(hours=10),
                      dates[11] + pd.Timedelta(hours=11)],
        "close": [thresh * 0.5, thresh * 0.9],
    })}
    res = run_regime_router(ch, cfg, states, intraday=intr)

    # sanity: the staged path actually fired
    assert res.intraday_tp_fills == 1
    assert any(t.action == "BUY_SHARES" for t in res.trades)
    assert any(t.action == "SELL_SHARES" for t in res.trades)
    # the payload: buy at date idx 11, sell at idx 12 -> one whipsaw pair.
    # Shadow bug corrupts trend_buy_idx to 0 -> 12 - 0 = 12 > 10 -> 0 pairs.
    assert res.whipsaw_pairs == 1
