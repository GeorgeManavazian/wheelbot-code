"""Cross-sectional momentum rotation (Clenow Trading Evolved ch 12, adapted
from S&P 500 stocks to our fixed ETF basket — no index-membership machinery).

Score = annualized exponential regression slope * R^2: rewards strong AND
smooth trends. Monthly: hold top_n by score (score > min_score), weighted by
inverse 20d volatility (equal-risk-ish). Nothing qualifies -> cash.

NB: min_score is window-length dependent (Clenow used 40 on 125d stock data);
default 0 = 'any positive momentum', sweep it in the batch runner.
"""
import numpy as np
import pandas as pd
from scipy import stats

from src.strategies.base import Strategy


def momentum_score(closes: pd.Series) -> float:
    y = np.log(closes.values)
    x = np.arange(len(y))
    slope, _, r, _, _ = stats.linregress(x, y)
    return (np.exp(slope) ** 252 - 1) * 100 * r**2


class MomentumRotation(Strategy):
    name = "momentum_rotation"
    DEFAULTS = {"lookback": 125, "top_n": 3, "vol_window": 20, "min_score": 0.0}
    description = """\
**What it does:** Once a month, ranks every ETF by trend quality — the slope
of its recent price path times how smooth that path is (regression R²). Buys
the `top_n` best scorers above `min_score`, giving smaller weights to the
more volatile ones. If nothing scores well, holds cash.

**Why it should work:** Cross-sectional momentum — recent winners keep
winning over 1–12 month horizons — is one of the most documented effects in
markets. The smoothness filter prefers steady climbers over one-headline
spikes, and inverse-volatility sizing keeps any single holding from
dominating risk.

**When it fails:** Momentum crashes — violent reversals after panics, where
beaten-down losers rocket and past winners lag. Holding only 2–3 ETFs means
one bad holding hurts; monthly rebalancing reacts slowly to fast turns.
"""

    def target_weights(self, window):
        if not self.is_month_start(window):
            return None
        look = self.params["lookback"]
        if len(window) <= look:
            return None

        recent = window.iloc[-look:]
        scores = recent.apply(momentum_score)
        top = scores.nlargest(self.params["top_n"])
        top = top[top > self.params["min_score"]]
        if top.empty:
            return {}

        vol = (window[top.index].pct_change()
               .iloc[-self.params["vol_window"]:].std())
        vol = vol.clip(lower=1e-9)  # a 20d flat price would give inf weight
        inv = 1.0 / vol
        weights = inv / inv.sum()
        return weights.to_dict()
