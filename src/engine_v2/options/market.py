"""The Market seam: step_one_day reads market data through this interface, so
the SAME per-day logic runs over full backtest history (BatchMarket) or today's
live data + carried prices (LiveMarket, sub-project B2). BatchMarket preserves
the batch backtest's exact behavior — including the on-or-before-expiry history
reach in settle_price."""
from __future__ import annotations
import pandas as pd
from .wheel import GATE_STALENESS_DAYS   # single source of truth (no silent drift)


def _row_before(states: pd.DataFrame, d):
    """Full state row strictly before d, staleness-bounded. None -> unknown."""
    idx = states.index
    pos = idx.searchsorted(pd.Timestamp(d)) - 1
    if pos < 0 or (pd.Timestamp(d) - idx[pos]).days > GATE_STALENESS_DAYS:
        return None
    return states.iloc[pos]


class BatchMarket:
    """Market over fully-preloaded backtest chains + regime states."""

    def __init__(self, chains: dict, regime_states: dict, clean_start: dict,
                 universe: list, earnings=None):
        self._und = {t: chains[t].groupby("date")["underlying"].first()
                     for t in universe}
        self._by_date = {t: {pd.Timestamp(k): g
                             for k, g in chains[t].groupby("date")}
                         for t in universe}
        self._states = regime_states
        self._clean_start = clean_start
        self._universe = list(universe)
        self._earnings = earnings

    @property
    def universe(self) -> list:
        return self._universe

    def chain(self, ticker, day):
        return self._by_date[ticker].get(pd.Timestamp(day))

    def spot(self, ticker, day, fallback):
        u = self._und[ticker]
        d = pd.Timestamp(day)
        return float(u[d]) if d in u.index else fallback

    def settle_price(self, ticker, expiry):
        u = self._und[ticker]
        pre = u[u.index <= pd.Timestamp(expiry)]
        return float(pre.iloc[-1]) if len(pre) else None

    # A15: batch-only bounded settlement fall-back. The refuse-and-warn
    # settlement rule is correct live (a later run with restored history
    # settles the leg), but the batch driver HAS no later run -- a ticker
    # whose history lacks the expiry date zombied the leg to the end of the
    # backtest and handed it to the residual finalizer at a carried ask.
    # Last close within SETTLE_REACHBACK_DAYS calendar days at/before expiry;
    # None beyond the bound (never settle on stale territory). LiveMarket
    # deliberately does NOT grow this method: step_one_day falls through only
    # when the market provides it, so live behavior is byte-identical.
    SETTLE_REACHBACK_DAYS = 5

    def bounded_settle_price(self, ticker, expiry):
        u = self._und[ticker]
        exp = pd.Timestamp(expiry)
        pre = u[(u.index <= exp)
                & (u.index >= exp - pd.Timedelta(days=self.SETTLE_REACHBACK_DAYS))]
        return float(pre.iloc[-1]) if len(pre) else None

    def regime_row(self, ticker, day):
        return _row_before(self._states[ticker], day)

    def eligible(self, ticker, day) -> bool:
        cs = self._clean_start.get(ticker)
        return cs is None or pd.Timestamp(day) >= pd.Timestamp(cs)

    def earnings_dates(self, ticker):
        """Scheduled prints for the blackout gate. None = unknown (allow, and
        the caller counts it); [] = known to have none. Deliberately a separate
        method from eligible(): eligible() means "this ticker's stored chain is
        trustworthy from here", a data-integrity fact, and overloading it with a
        strategy veto would make blocked entries invisible in the run log."""
        return None if self._earnings is None else self._earnings.dates(ticker)
