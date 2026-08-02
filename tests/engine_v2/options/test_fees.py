"""A13: model exchange/OCC/regulatory pass-through fees and the per-event
assignment/exercise fee. One derived constant -- friction_per_contract =
commission + fees -- consumed at the four shared arithmetic sites (A18 lesson:
one place, not four engines), so the tp_exit_floor guard always prices exactly
the friction the fills charge. Defaults 0.0: the plain path is byte-identical
and every golden stays pinned. Production values live in FROZEN
(fees_per_contract=0.05 provisional, owner-verify against a real statement;
fee_per_assignment=0.00, Schwab charges nothing since 2019 -- VERIFY).
"""
import pandas as pd
import pytest

from src.engine_v2.options.chain import Contract
from src.engine_v2.options.portfolio import PortfolioState, step_one_day
from src.engine_v2.options.wheel import (WheelConfig, run_wheel,
                                         sell_proceeds, buy_cost)
from src.engine_v2.options.fills import (tp_exit_floor, tp_exit_feasible,
                                         try_take_profit)
from src.engine_v2.options.regime_router import run_regime_router

COLS = ["date", "expiry", "dte", "strike", "right", "bid", "ask", "mid",
        "close", "delta", "iv", "underlying"]


class _Mark:
    def __init__(self, bid, ask):
        self.bid, self.ask, self.mid = bid, ask, (bid + ask) / 2


def _chain(rows):
    ch = pd.DataFrame(rows, columns=COLS)
    ch["date"] = pd.to_datetime(ch["date"])
    ch["expiry"] = pd.to_datetime(ch["expiry"])
    return ch


# ---- the derived constant and the two proceeds helpers ----

def test_friction_is_commission_plus_fees():
    cfg = WheelConfig(commission_per_contract=0.65, fees_per_contract=0.05)
    assert cfg.friction_per_contract == pytest.approx(0.70)
    assert WheelConfig().friction_per_contract == pytest.approx(0.65)


def test_sell_proceeds_and_buy_cost_charge_fees():
    cfg = WheelConfig(commission_per_contract=0.65, fees_per_contract=0.05)
    m = _Mark(bid=1.00, ask=1.10)
    assert sell_proceeds(m, 2, cfg) == pytest.approx(200.0 - 1.30 - 0.10)
    assert buy_cost(m, 2, cfg) == pytest.approx(220.0 + 1.30 + 0.10)


def test_fee_default_zero_is_byte_identical():
    cfg = WheelConfig()
    m = _Mark(bid=1.00, ask=1.10)
    assert sell_proceeds(m, 2, cfg) == pytest.approx(200.0 - 1.30)
    assert buy_cost(m, 2, cfg) == pytest.approx(220.0 + 1.30)


# ---- the TP-close seam charges the same friction ----

def test_tp_close_cost_includes_fees_quote_mode():
    cfg = WheelConfig(take_profit_pct=0.50, commission_per_contract=0.65,
                      fees_per_contract=0.05)
    dec = try_take_profit(mark=_Mark(0.30, 0.40), credit=1.00, contracts=3,
                          cfg=cfg, day=pd.Timestamp("2024-01-03"),
                          expiry=pd.Timestamp("2024-01-19"))
    assert dec.filled and dec.via == "quote"
    assert dec.cost == pytest.approx(0.40 * 100 * 3 + 0.70 * 3)


def test_tp_close_cost_includes_fees_print_mode():
    cfg = WheelConfig(take_profit_pct=0.50, commission_per_contract=0.65,
                      fees_per_contract=0.05)
    day = pd.Timestamp("2024-01-03")
    bars = pd.DataFrame({
        "timestamp": [day + pd.Timedelta(hours=10), day + pd.Timedelta(hours=11)],
        "close": [0.45, 0.42]})
    dec = try_take_profit(mark=None, credit=1.00, contracts=3, cfg=cfg,
                          day=day, expiry=pd.Timestamp("2024-01-19"), bars=bars)
    assert dec.filled and dec.via == "print"
    assert dec.cost == pytest.approx(0.42 * 100 * 3 + 0.70 * 3)


# ---- floor/fill consistency: the guard prices what the fills charge ----

def test_tp_exit_floor_rises_with_fees():
    lo = tp_exit_floor(WheelConfig(take_profit_pct=0.60,
                                   commission_per_contract=0.65))
    hi = tp_exit_floor(WheelConfig(take_profit_pct=0.60,
                                   commission_per_contract=0.65,
                                   fees_per_contract=0.30))
    assert hi > lo
    # the commission leg of the floor uses friction, term for term
    assert hi == pytest.approx(max(0.01 / 0.40, 2.0 * 0.95 / (0.60 * 100)))


def test_credit_fine_without_fees_refused_with_fees():
    """A credit that clears the no-fee floor but is a guaranteed net loss
    once fees are charged must be refused -- otherwise the bot writes exits
    the fills arithmetic loses money on (the one real defect class A13 could
    introduce, per the analyst)."""
    tp = 0.60
    base = dict(take_profit_pct=tp, commission_per_contract=0.65)
    no_fee = WheelConfig(**base)
    fee = WheelConfig(**base, fees_per_contract=1.00)   # exaggerated to split
    credit = tp_exit_floor(no_fee) + 0.001
    assert tp_exit_feasible(credit, no_fee)[0]
    ok, why = tp_exit_feasible(credit, fee)
    assert not ok and why == "tp_net_negative"


# ---- the per-event assignment fee, all three engines ----

_PUT_ITM = [
    ["2024-01-02", "2024-01-09", 7, 470, "P", 2.00, 2.10, 2.05, 2.05, -0.30, 0.1, 472.0],
    ["2024-01-09", "2024-01-09", 0, 470, "P", 5.00, 5.10, 5.05, 5.05, -0.99, 0.1, 465.0],
]


def test_wheel_assignment_charges_event_fee():
    fee = WheelConfig(starting_capital=50_000.0, put_delta=0.30,
                      take_profit_pct=None, commission_per_contract=0.0,
                      fee_per_assignment=15.0)
    res = run_wheel(_chain(_PUT_ITM), fee)
    # +200 credit, -47000 assigned, -15 assignment fee
    assert res.final_cash == pytest.approx(50_000 + 200 - 47_000 - 15.0)


def test_wheel_called_away_charges_event_fee():
    rows = _PUT_ITM + [
        ["2024-01-10", "2024-01-16", 6, 475, "C", 3.00, 3.10, 3.05, 3.05, 0.30, 0.1, 465.0],
        ["2024-01-16", "2024-01-16", 0, 475, "C", 5.00, 5.10, 5.05, 5.05, 0.99, 0.1, 480.0],
    ]
    fee = WheelConfig(starting_capital=50_000.0, put_delta=0.30, call_delta=0.30,
                      take_profit_pct=None, commission_per_contract=0.0,
                      fee_per_assignment=15.0)
    res = run_wheel(_chain(rows), fee)
    # +200; -47000-15 assigned; +300 call; +47500-15 called away
    assert res.final_cash == pytest.approx(
        50_000 + 200 - 47_015 + 300 + 47_500 - 15.0)


def test_router_assignment_charges_event_fee():
    states = pd.DataFrame({"trend": "chop", "vol": "normal", "vol_pctile": 0.5},
                          index=pd.DatetimeIndex(["2024-01-01"]))
    fee = WheelConfig(starting_capital=50_000.0, put_delta=0.30,
                      take_profit_pct=None, commission_per_contract=0.0,
                      call_min_strike="basis", fee_per_assignment=15.0)
    res = run_regime_router(_chain(_PUT_ITM), fee, states)
    assert res.final_cash == pytest.approx(50_000 + 200 - 47_000 - 15.0)


def test_portfolio_assignment_charges_event_fee():
    class _M:
        universe = []

        def chain(self, tk, d):
            return None

        def spot(self, tk, d, fb):
            return 465.0

        def settle_price(self, tk, e):
            return 465.0

        def regime_row(self, tk, d):
            return None

        def eligible(self, tk, d):
            return True

    d = pd.Timestamp("2024-01-09")
    put = Contract("SPY", d, 470.0, "P")
    pos = {"ticker": "SPY", "shares": 0, "phase": "PUT", "basis": None,
           "premium": 200.0, "campaign": 1, "last_spot": 472.0,
           "short": {"contract": put, "contracts": 1, "credit": 2.0,
                     "last_mid": 2.0}}
    state = PortfolioState(cash=50_200.0, positions=[pos], campaign=1)
    cfg = WheelConfig(ticker="SPY", starting_capital=50_000.0,
                      take_profit_pct=None, commission_per_contract=0.0,
                      fee_per_assignment=15.0)
    step_one_day(state, _M(), d, cfg, selector="plain", n_slots=1)
    assert state.cash == pytest.approx(50_200 - 47_000 - 15.0)


def test_assignment_fee_default_zero_changes_nothing():
    plain = WheelConfig(starting_capital=50_000.0, put_delta=0.30,
                        take_profit_pct=None, commission_per_contract=0.0)
    res = run_wheel(_chain(_PUT_ITM), plain)
    assert res.final_cash == pytest.approx(50_000 + 200 - 47_000)
