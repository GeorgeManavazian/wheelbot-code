import pandas as pd
import pytest

from src.engine.fills import execute_rebalance


def test_buy_from_cash_no_slippage():
    pos, cash, trades = execute_rebalance(
        {}, 10_000.0, {"A": 0.5}, pd.Series({"A": 100.0}), slippage_bps=0)
    assert pos == {"A": 50}
    assert cash == pytest.approx(5_000.0)
    assert trades == [{"ticker": "A", "side": "buy", "shares": 50, "price": 100.0}]


def test_buy_pays_slippage():
    # 100 bps: buy price 101 -> floor(5000/101) = 49 shares
    pos, cash, trades = execute_rebalance(
        {}, 10_000.0, {"A": 0.5}, pd.Series({"A": 100.0}), slippage_bps=100)
    assert pos == {"A": 49}
    assert cash == pytest.approx(10_000.0 - 49 * 101.0)


def test_sell_receives_slippage_penalty():
    # sell all of A at 100 with 100 bps -> proceeds 99/share
    pos, cash, trades = execute_rebalance(
        {"A": 10}, 0.0, {"A": 0.0}, pd.Series({"A": 100.0}), slippage_bps=100)
    assert pos == {}
    assert cash == pytest.approx(990.0)
    assert trades[0]["side"] == "sell"


def test_sells_execute_before_buys():
    # all cash in A; switch to B. Must sell A first to afford B.
    pos, cash, trades = execute_rebalance(
        {"A": 100}, 0.0, {"A": 0.0, "B": 1.0},
        pd.Series({"A": 100.0, "B": 100.0}), slippage_bps=0)
    assert pos == {"B": 100}
    assert [t["side"] for t in trades] == ["sell", "buy"]


def test_insufficient_cash_reduces_buy(capsys):
    # full switch A -> B with 100 bps slippage: sell 100 A @ 99 -> cash 9900,
    # target B = floor(10000/101) = 99 shares costing 9999 -> capped to 98
    pos, cash, trades = execute_rebalance(
        {"A": 100}, 0.0, {"B": 1.0},
        pd.Series({"A": 100.0, "B": 100.0}), slippage_bps=100)
    assert pos == {"B": 98}
    assert cash == pytest.approx(9_900.0 - 98 * 101.0)
    assert "WARNING" in capsys.readouterr().out


def test_weights_over_one_rejected():
    with pytest.raises(ValueError, match="sum"):
        execute_rebalance({}, 1000.0, {"A": 0.6, "B": 0.6},
                          pd.Series({"A": 10.0, "B": 10.0}), slippage_bps=0)


def test_negative_weight_rejected():
    with pytest.raises(ValueError, match="short"):
        execute_rebalance({}, 1000.0, {"A": -0.1}, pd.Series({"A": 10.0}),
                          slippage_bps=0)


def test_unknown_ticker_rejected():
    with pytest.raises(ValueError, match="price"):
        execute_rebalance({}, 1000.0, {"ZZZ": 0.5}, pd.Series({"A": 10.0}),
                          slippage_bps=0)


def test_no_trade_when_already_at_target():
    pos, cash, trades = execute_rebalance(
        {"A": 50}, 5_000.0, {"A": 0.5}, pd.Series({"A": 100.0}), slippage_bps=0)
    assert trades == []
