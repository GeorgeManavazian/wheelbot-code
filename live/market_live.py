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
    def __init__(self, universe, held_tickers, obs_date, *, closes_fn, chain_fn,
                 chop_max_ma_spread=None, chop_max_fast_spread=None,
                 chop_max_fast_fall=None):
        self._universe = list(universe)
        self._obs = pd.Timestamp(obs_date).normalize()
        self._chop_max_ma_spread = chop_max_ma_spread
        self._chop_max_fast_spread = chop_max_fast_spread
        self._chop_max_fast_fall = chop_max_fast_fall
        self._closes = {}   # ticker -> close Series
        self._rows = {}     # ticker -> prior-day regime row (or None)
        self._chains = {}   # ticker -> chain df (only pulled ones)
        # Two DIFFERENT populations -- keep them apart. Closes are pulled for the
        # whole universe; chains only for held+good-to-rent (~5%). Pooling them
        # into one `skipped` list made the zombie gate structurally unable to see
        # a total chain outage: 28/547 can never reach a 50% threshold, so a day
        # with zero usable chains was stamped complete and lost. (audit 2026-07-29)
        self.skipped_closes = []   # universe tickers whose price history failed
        self.skipped_chains = []   # candidate tickers whose option chain failed
        self.chain_attempts = 0    # how many chains we TRIED to pull

        good = set()
        for tk in self._universe:
            try:
                s = closes_fn(tk)
                row = _row_before(regime_series(s), self._obs)   # regime compute
            except Exception as e:   # one bad symbol must not stop the bot
                self.skipped_closes.append((tk, str(e)))         # (also catches a
                continue                                          # regime_series raise)
            self._closes[tk] = s
            self._rows[tk] = row
            if is_good_renting_weather(row, self._chop_max_ma_spread,
                                       self._chop_max_fast_spread,
                                       self._chop_max_fast_fall):
                good.add(tk)

        for tk in (set(held_tickers) | good) & set(self._closes):
            self.chain_attempts += 1
            try:
                self._chains[tk] = chain_fn(tk)
            except Exception as e:
                self.skipped_chains.append((tk, str(e)))

    @property
    def skipped(self):
        """Every failed pull, both kinds. Kept for logging/back-compat only --
        callers deciding whether the RUN failed must use the two lists
        separately, because they are drawn from different-sized populations."""
        return self.skipped_closes + self.skipped_chains

    @property
    def chains_ok(self):
        return len(self._chains)

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
        # last close on-or-before expiry (matches BatchMarket) so a MISSED-day /
        # late-resolved expiry settles at the expiry-day close, not a later run's
        # spot. Returns None only if no close exists on/before the expiry.
        s = self._closes.get(ticker)
        if s is None:
            return None
        pre = s[s.index <= pd.Timestamp(expiry)]
        return float(pre.iloc[-1]) if len(pre) else None

    def regime_row(self, ticker, day):
        return self._rows.get(ticker)

    def eligible(self, ticker, day):
        return True
