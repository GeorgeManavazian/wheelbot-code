"""LiveMarket: the Market interface backed by TODAY's Schwab data + carried
prices, so the same step_one_day that ran the backtest runs live. Efficient by
construction — pulls chains only for held ∪ good-to-rent tickers (step's routing
loop asks chain() for every universe ticker; a non-candidate returns None and is
skipped, same outcome as the regime check). closes_fn/chain_fn are injected so
tests use fixtures, prod uses the Schwab client + adapter."""
from __future__ import annotations
import pandas as pd
from src.engine_v2.regime.state import regime_series, is_good_renting_weather
from src.engine_v2.options.market import _row_before


class LiveMarket:
    def __init__(self, universe, held_tickers, obs_date, *, closes_fn, chain_fn):
        self._universe = list(universe)
        self._obs = pd.Timestamp(obs_date).normalize()
        self._closes = {}   # ticker -> close Series
        self._rows = {}     # ticker -> prior-day regime row (or None)
        self._chains = {}   # ticker -> chain df (only pulled ones)
        self.skipped = []   # tickers whose pull failed

        good = set()
        for tk in self._universe:
            try:
                s = closes_fn(tk)
            except Exception as e:   # one bad symbol must not stop the bot
                self.skipped.append((tk, str(e)))
                continue
            self._closes[tk] = s
            row = _row_before(regime_series(s), self._obs)
            self._rows[tk] = row
            if is_good_renting_weather(row):
                good.add(tk)

        for tk in (set(held_tickers) | good) & set(self._closes):
            try:
                self._chains[tk] = chain_fn(tk)
            except Exception as e:
                self.skipped.append((tk, str(e)))

    @property
    def universe(self):
        return self._universe

    def chain(self, ticker, day):
        return self._chains.get(ticker)

    def spot(self, ticker, day, fallback):
        s = self._closes.get(ticker)
        if s is None:
            return fallback
        d = pd.Timestamp(day).normalize()
        return float(s[d]) if d in s.index else fallback

    def settle_price(self, ticker, expiry):
        return None   # live runs daily; step settles at today's spot

    def regime_row(self, ticker, day):
        return self._rows.get(ticker)

    def eligible(self, ticker, day):
        return True
