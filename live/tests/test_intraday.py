import pandas as pd
from src.engine_v2.options.chain import Contract, Mark
from src.engine_v2.options.portfolio import PortfolioState
from src.engine_v2.options.wheel import WheelConfig
from live.intraday import manage_intraday

CFG = WheelConfig(ticker="SPY", take_profit_pct=0.60, put_delta=0.30,
                  call_delta=0.50, target_dte=11, call_min_strike="basis")
NOW = pd.Timestamp("2026-07-21 12:00")   # midday, before expiry


def _put_state(credit=1.00, n=10, expiry="2026-08-15", cash=100_000.0):
    c = Contract("AGNC", pd.Timestamp(expiry), 11.0, "P")
    pos = {"ticker": "AGNC", "shares": 0, "phase": "PUT", "basis": None,
           "premium": credit * 100 * n, "campaign": 1,
           "last_spot": 11.0,
           "short": {"contract": c, "contracts": n, "credit": credit, "last_mid": credit}}
    return PortfolioState(cash=cash, positions=[pos])


def test_closes_put_at_tp_on_live_ask():
    st = _put_state()
    # TP 60% -> close when ask <= 0.40. Live ask 0.40 hits exactly.
    quotes = {"AGNC": Mark(0.38, 0.40, 0.39)}
    trades = manage_intraday(st, quotes, CFG, NOW)
    assert len(trades) == 1 and trades[0].action == "CLOSE_PUT"
    assert trades[0].price_per_contract == 0.40          # booked at the ask
    # cash -= buy_cost = 0.40*100*10 + 0.65*10 = 406.5
    assert round(st.cash, 2) == round(100_000 - 406.5, 2)
    assert st.positions == []                            # emptied slot dropped


def test_holds_when_above_tp():
    st = _put_state()
    quotes = {"AGNC": Mark(0.48, 0.50, 0.49)}            # ask 0.50 > 0.40
    trades = manage_intraday(st, quotes, CFG, NOW)
    assert trades == []
    assert st.cash == 100_000.0
    assert len(st.positions) == 1


def test_skips_when_no_quote():
    st = _put_state()
    trades = manage_intraday(st, {}, CFG, NOW)            # market closed / no data
    assert trades == [] and len(st.positions) == 1


def test_ignores_expiry_day_and_past():
    st = _put_state(expiry="2026-07-21")                  # expires today
    quotes = {"AGNC": Mark(0.00, 0.01, 0.005)}            # deep ITP but expiry -> EOD
    trades = manage_intraday(st, quotes, CFG, NOW)
    assert trades == [] and len(st.positions) == 1


def test_no_tp_config_is_noop():
    st = _put_state()
    cfg = WheelConfig(ticker="SPY", take_profit_pct=None)
    assert manage_intraday(st, {"AGNC": Mark(0.0, 0.01, 0.0)}, cfg, NOW) == []
