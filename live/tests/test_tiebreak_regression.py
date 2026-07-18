"""Regression: the routing tie-break must work for ANY live-universe ticker, not
only the 9 backtest tickers in ROTATION_TIE_ORDER. Before the market.universe.index
fix, a good-to-rent ticker outside ROTATION_TIE_ORDER crashed step_one_day with
ValueError on the tie-break. This pins that it routes cleanly."""
import pandas as pd
from src.engine_v2.options.portfolio import PortfolioState, step_one_day
from src.engine_v2.options.wheel import WheelConfig


class _StubMarket:
    """universe = a single NON-ROTATION_TIE_ORDER ticker in chop weather with a
    sellable put — forces the tie-break on a ticker absent from ROTATION_TIE_ORDER."""
    universe = ["FOO"]   # deliberately NOT in ROTATION_TIE_ORDER
    def __init__(self):
        self._obs = pd.Timestamp("2026-07-17")
        self._chain = pd.DataFrame([{
            "date": self._obs, "expiry": pd.Timestamp("2026-07-28"),
            "strike": 20.0, "right": "P", "dte": 11, "delta": -0.30,
            "bid": 0.60, "ask": 0.70, "mid": 0.65, "underlying": 22.0}])
    def chain(self, tk, d): return self._chain if tk == "FOO" else None
    def spot(self, tk, d, fb): return 22.0
    def settle_price(self, tk, e): return None
    def regime_row(self, tk, d):
        return {"trend": "chop", "vol": "normal", "vol_pctile": 0.5}
    def eligible(self, tk, d): return True


def test_non_rotation_ticker_routes_without_crash():
    cfg = WheelConfig(ticker="FOO", put_delta=0.30, call_delta=0.50, target_dte=11,
                      take_profit_pct=0.60, starting_capital=100_000.0,
                      call_min_strike="basis")
    state = PortfolioState(cash=100_000.0, positions=[])
    # would raise ValueError on the old ROTATION_TIE_ORDER.index("FOO"); must not now
    r = step_one_day(state, _StubMarket(), pd.Timestamp("2026-07-17"), cfg,
                     selector="chop", n_slots=1)
    assert [t.action for t in r.trades] == ["SELL_PUT"]
    assert state.positions[0]["ticker"] == "FOO"
