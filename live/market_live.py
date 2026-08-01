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
        # B8: short-but-valid histories (recently listed name, truncated pull)
        # compute NO regime row -> permanently entry-ineligible. That is
        # correct behavior but must be VISIBLE: recorded here + logged, never
        # in skipped_closes (would poison the zombie denominators and drop
        # closes a held ticker needs for marks/settlement).
        self.truncated_closes = []  # (ticker, n_bars)

        good = set()
        # B5: a held ticker that fell OUT of the universe must still get
        # closes (marks, settlement) and a chain (take-profit) -- otherwise
        # the position freezes silently forever. Universe order first, held
        # extras after (stable, deduped).
        for tk in list(dict.fromkeys(self._universe + sorted(set(held_tickers)))):
            try:
                s = closes_fn(tk)
                rs = regime_series(s)                            # regime compute
                row = _row_before(rs, self._obs)
            except Exception as e:   # one bad symbol must not stop the bot
                self.skipped_closes.append((tk, str(e)))         # (also catches a
                continue                                          # regime_series raise)
            self._closes[tk] = s
            self._rows[tk] = row
            if row is None and rs.empty:
                # rs.empty, NOT len(s) <= WARMUP: the first regime row needs
                # the vol percentile too and lands ~273 trading days in, so a
                # length check at WARMUP=200 left the 201-273 band silently
                # ineligible -- the exact B8 class, one bar above the check
                # (skeptic F1). A stale-long history has rs non-empty and is
                # correctly NOT flagged here.
                self.truncated_closes.append((tk, len(s)))
                print(f"closes truncated: {tk} has {len(s)} bars -- no regime "
                      f"state computable yet; entry-ineligible until history "
                      f"accrues")
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

    def add_chain_rows(self, ticker, rows) -> int:
        """Splice extra contract rows into a pulled chain; returns how many were
        actually added. Used to mark held legs that fall outside the bounded
        strike window (see live/held_legs.py).

        Rows for a (expiry, strike, right) the chain already carries are DROPPED,
        not overwritten — the chain pull is the day's authoritative snapshot. A
        ticker with no chain at all is skipped rather than given a synthetic
        one-row chain, because `day_chain is not None` is what gates the
        covered-call branch, and a chain holding only the leg we already hold
        would be a misleading thing to hand it."""
        existing = self._chains.get(ticker)
        # `len(existing) == 0` matters as much as `is None`: chain_from_json
        # returns an EMPTY frame, not None, when every contract is filtered out
        # (bid<=0 / ask<=0 / missing delta — routine for a thin name). Splicing
        # into that frame would hand the engine a chain whose ONLY row is the leg
        # already held, which select_contract then picks by default: a strike the
        # bot never surveyed, at whatever credit the quote carried — including
        # $0.00, which makes the resulting leg permanently unclosable, because
        # the take-profit test becomes `ask <= 0`. (audit 2026-07-31)
        if existing is None or len(existing) == 0 or not rows:
            return 0
        have = set(zip(existing["expiry"], existing["strike"], existing["right"]))
        fresh = [r for r in rows if (r["expiry"], r["strike"], r["right"]) not in have]
        if not fresh:
            return 0
        add = pd.DataFrame(fresh)
        # Union of columns, NOT `columns=existing.columns` — that silently drops
        # any column the pulled chain lacks, which would have thrown away the
        # `held_only` flag that keeps a mark-only row out of select_contract.
        merged = pd.concat([existing, add], ignore_index=True)
        if "held_only" in merged.columns:
            # existing rows predate the flag; absent means tradeable
            merged["held_only"] = merged["held_only"].fillna(False).astype(bool)
        # Schwab may omit delta (live/data.py documents its "NaN" string), and
        # _num turns that into None. A None in the column flips the whole thing
        # to object dtype, and select_contract's `e["delta"].abs()` then raises
        # TypeError — which run_daily catches PER ACCOUNT, silently gapping every
        # account that routes this ticker. Coerce so the column stays numeric and
        # a missing delta is NaN, which sorts out of contention on its own.
        merged["delta"] = pd.to_numeric(merged["delta"], errors="coerce")
        self._chains[ticker] = merged
        return len(fresh)

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

    def prior_close(self, ticker, day):
        """A10 capability: the FRESH series' close at a past date, exact-date
        only. Schwab restates history retroactively on a split, so comparing
        this against the spot the engine STORED that day is the corporate-
        action detector. BatchMarket deliberately never grows this method
        (hasattr-pinned) -- a batch series cannot restate against itself."""
        s = self._closes.get(ticker)
        if s is None:
            return None
        d = pd.Timestamp(day)
        try:
            v = s.loc[d]
        except KeyError:
            return None
        v = float(v.iloc[-1] if hasattr(v, "iloc") else v)
        return v if v > 0 else None

    def regime_row(self, ticker, day):
        return self._rows.get(ticker)

    def eligible(self, ticker, day):
        return True
