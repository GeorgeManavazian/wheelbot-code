"""Time-series trend (Clenow Trading Evolved ch 16, long-only ETF adaptation).

Monthly: long each ETF whose close > close `lookback` bars ago, at 1/N_universe
weight each. Unqualified slots stay in cash — exposure auto-scales in bears.
"""
from src.strategies.base import Strategy


class TSTrend(Strategy):
    name = "ts_trend"
    display_name = "Trend Following"
    DEFAULTS = {"lookback": 125}
    description = """\
**What it does:** Once a month, checks each ETF in the basket: is today's
price higher than it was `lookback` trading days ago? Every ETF that passes
gets an equal slice (1/20th) of the account. Slots that fail stay in cash,
so market exposure shrinks automatically in bear markets.

**Why it should work:** Trends persist. Assets that have been rising keep
rising slightly more often than chance — classic explanations are investor
under-reaction to news and herding. Holding only what is already rising
sidesteps the worst of long bear legs.

**When it fails:** Choppy, sideways markets — the strategy buys strength
that immediately fades, over and over (whipsaw). Sharp V-shaped crashes and
rebounds (March 2020): it exits near the bottom and re-enters only after
much of the recovery is gone.
"""

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
