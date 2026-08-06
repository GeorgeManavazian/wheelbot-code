"""IV rank (Natenberg's relative volatility rank) + the entry predicate.

The gate's claim: the bot loads `iv` on every contract (chain.py:43) and then
never reads it, ranking candidates on REALIZED vol instead -- it measures the
risk taken and never the price paid for taking it. Measured 2026-08-04 over
42,226 gated ticker-days on 153 names, mean return on collateral is -11.8 bps
across all entries and turns positive only in the top decile of IV rank
(+13.7 bps at >= 0.90), monotonically, and in all three years separately.
See tests/engine_v2/options/test_iv_rank.py for the full table.

THREE states per ticker, kept apart for the same reason earnings.py keeps
them apart -- "no history" and "lookup failed" both present as nothing, and
collapsing them is how a gate silently stops existing (zombie_check, audit
2026-07-29):

    rank(tk, d) -> 0.0..1.0   ranked against its own trailing window
    rank(tk, d) -> None       UNKNOWN: absent ticker, or too few trailing obs

Unknown does not block, per the standing phase-1/2 convention -- but it is
returned under its own reason string so callers COUNT it rather than shrug.

LOOK-AHEAD: rank() slices the series to observations at or before `obs_date`
ITSELF. A caller cannot forget to do it, and cannot pass a pre-sliced series
that is secretly too long. This is the whole reason history is a class rather
than a dict of floats.
"""
from __future__ import annotations

import pandas as pd

# Two years of daily observations, matching Natenberg's relative-volatility
# rank. Not a tuning knob: it is the definition of the statistic.
RANK_WINDOW = 252
# Below this many trailing observations a percentile is not a percentile. A
# name ranked against 12 days would qualify or fail on noise.
MIN_RANK_OBS = 150
# Where an UNMEASURABLE rank sorts when IV rank is the sort key (owner
# decision 2026-08-05). A veto can wave unknowns through harmlessly; a sort
# must physically place them. Sorting them last would silently de-prioritise
# every thin-history name -- a filter wearing a sort's clothes, which is the
# exact failure mode that killed the veto (campaign count 254 -> 130). Neutral
# means neutral in both directions: it outranks a measurably cheap name and
# loses to a measurably rich one. NOT a tuning knob -- it is the midpoint of a
# percentile, and moving it re-introduces the bias it exists to avoid.
NEUTRAL_IV_RANK = 0.5


class IVHistory:
    """Ticker -> that ticker's IV series, indexed by observation date.

    Absent ticker means unknown, not calm -- see the module docstring.
    """

    def __init__(self, by_ticker: dict | None = None,
                 window: int = RANK_WINDOW, min_obs: int = MIN_RANK_OBS):
        self._by = {t: pd.Series(s).sort_index()
                    for t, s in (by_ticker or {}).items()}
        self._window = window
        self._min_obs = min_obs

    @classmethod
    def from_chains(cls, chains: dict, cfg, window: int = RANK_WINDOW,
                    min_obs: int = MIN_RANK_OBS) -> "IVHistory":
        """Build the ranked series from the backtest chains themselves.

        The recorded value is the IV of the contract `select_contract` WOULD
        sell that day -- same delta target, same DTE band -- not a chain-wide
        average and not an ATM proxy. That matters: the floor was measured on
        exactly this quantity, so ranking anything else silently redefines it.

        A date with no selectable put contributes no observation rather than a
        NaN, so gaps shorten the history instead of poisoning the percentile.
        """
        from .select import select_contract      # local: select imports chain

        by = {}
        for tk, ch in chains.items():
            ch = ch.copy()
            ch["date"] = pd.to_datetime(ch["date"])
            vals = {}
            for d, day in ch.groupby("date"):
                c = select_contract(day, d, "P", cfg.put_delta, cfg.target_dte, tk)
                if c is None:
                    continue
                sel = day[(day["expiry"] == c.expiry)
                          & (day["strike"] == c.strike)
                          & (day["right"] == "P")]
                if sel.empty:
                    continue
                iv = float(sel["iv"].iloc[0])
                if pd.notna(iv):
                    vals[d] = iv
            if vals:
                by[tk] = pd.Series(vals)
        return cls(by, window=window, min_obs=min_obs)

    def rank(self, ticker: str, obs_date) -> float | None:
        """Fraction of the trailing window strictly below today's IV, or None
        when it cannot be measured. Reads no observation after `obs_date`."""
        s = self._by.get(ticker)
        if s is None:
            return None
        past = s.loc[:pd.Timestamp(obs_date)].dropna()
        if len(past) < self._min_obs:
            return None
        window = past.iloc[-self._window:]
        today = window.iloc[-1]
        prior = window.iloc[:-1]
        if len(prior) == 0:
            return None
        return float((prior < today).mean())

    def known(self, ticker: str) -> bool:
        return ticker in self._by

    def __len__(self) -> int:
        return len(self._by)


def iv_rank_ok(rank, cfg):
    """(allowed, reason) for a NEW short-put entry under the IV-rank floor.

    Mirrors yield_ok/credit_ok's shape: floor unset (None) -> always allowed,
    so the default path is byte-identical. An unmeasurable rank (None) ALLOWS,
    but says so under its own reason so the caller can count it.
    """
    floor = getattr(cfg, "min_iv_rank", None)
    if floor is None:
        return True, ""
    if rank is None:
        return True, "iv_rank_unknown"
    if rank < floor:
        return False, "iv_rank_below_floor"
    return True, ""
