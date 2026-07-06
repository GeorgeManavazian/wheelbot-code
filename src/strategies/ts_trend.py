"""Time-series trend (Clenow Trading Evolved ch 16, long-only ETF adaptation).

Monthly: long each ETF whose close > close `lookback` bars ago, at 1/N_universe
weight each. Unqualified slots stay in cash — exposure auto-scales in bears.
"""
from src.strategies.base import Strategy


class TSTrend(Strategy):
    name = "ts_trend"
    DEFAULTS = {"lookback": 125}

    def target_weights(self, window):
        if not self.is_month_start(window):
            return None
        look = self.params["lookback"]
        if len(window) <= look:
            return None
        today = window.iloc[-1]
        past = window.iloc[-1 - look]
        qualified = [t for t in window.columns if today[t] > past[t]]
        w = 1.0 / len(window.columns)
        return {t: w for t in qualified}
