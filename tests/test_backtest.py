import pandas as pd
import pytest

from src.engine.backtest import BacktestConfig, run_backtest


def make_long(opens, closes, ticker="A", start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(opens))
    return pd.DataFrame({
        "date": idx, "ticker": ticker, "open": opens, "high": closes,
        "low": opens, "close": closes, "volume": [1000] * len(opens)})


class BuyDayOneThenHold:
    name = "buyhold"
    params = {}
    def __init__(self):
        self.calls = []
    def target_weights(self, window):
        self.calls.append(window.index[-1])
        return {"A": 1.0} if len(window) == 1 else None


def test_fills_at_next_open_not_same_bar():
    # decision at day1 close; fill at day2 OPEN (110), never day1 close (100)
    df = make_long(opens=[100, 110, 110], closes=[100, 110, 120])
    res = run_backtest(df, BuyDayOneThenHold(),
                       BacktestConfig(initial_cash=11_000, slippage_bps=0))
    trade = res.trades.iloc[0]
    assert trade["price"] == 110.0          # day2 open
    assert trade["date"] == df["date"][1]   # day2
    assert trade["shares"] == 100           # floor(11000/110)


def test_equity_curve_hand_computed():
    df = make_long(opens=[100, 100, 100], closes=[100, 100, 110])
    res = run_backtest(df, BuyDayOneThenHold(),
                       BacktestConfig(initial_cash=10_000, slippage_bps=0))
    # day1: all cash 10000. day2: buy 100 @100 open, close 100 -> 10000.
    # day3: close 110 -> 11000.
    assert list(res.equity.values) == pytest.approx([10_000, 10_000, 11_000])
    assert res.holdings_value.iloc[-1] == pytest.approx(11_000)


def test_strategy_never_sees_future():
    df = make_long(opens=[100] * 5, closes=[100] * 5)
    strat = BuyDayOneThenHold()
    run_backtest(df, strat, BacktestConfig())
    dates = list(df["date"])
    # called once per bar except the last (no next open to fill at),
    # and window always ends at the decision date
    assert strat.calls == dates[:-1]


def test_window_mutation_cannot_corrupt_engine():
    class Mutator:
        name, params = "mut", {}
        def target_weights(self, window):
            window.iloc[:] = -1.0  # vandalize the window
            return None
    df = make_long(opens=[100] * 3, closes=[100] * 3)
    res = run_backtest(df, Mutator(), BacktestConfig(initial_cash=1_000))
    assert list(res.equity.values) == pytest.approx([1_000, 1_000, 1_000])


def test_nan_price_in_window_raises():
    df = make_long(opens=[100, 100, 100], closes=[100, float("nan"), 100])
    with pytest.raises(ValueError, match="NaN"):
        run_backtest(df, BuyDayOneThenHold(), BacktestConfig())
