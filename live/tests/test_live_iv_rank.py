"""IV rank on the LIVE market seam.

Why this file exists: `rank_by="iv_rank"` was measured on BatchMarket and the
live path was never wired. LiveMarket had no iv_rank() and run_daily built no
IVHistory, so flipping the flag in FROZEN would have ranked all ~531 candidates
NEUTRAL and picked by position in the universe list -- a full, plausible set of
trades answering a different question, with `entry_ranked_iv_unknown` in the log
as the only trace.

Everything here tests the LIVE entry point. The equivalent BatchMarket tests
pass today and passed then; that is exactly why they proved nothing about the
bot that actually runs.

See [[2026-08-06 - Wiring IV rank into the live bot, build brief]].
"""
import json
import pandas as pd
import pytest

from live.data import closes_from_json, chain_from_json
from live.market_live import LiveMarket
from src.engine_v2.options.iv_rank import IVHistory
from src.engine_v2.options.portfolio import PortfolioState, step_one_day
from src.engine_v2.options.wheel import WheelConfig

OBS = pd.Timestamp("2026-07-17")
PH = json.load(open("live/fixtures/price_history_gdx.json"))
OC = json.load(open("live/fixtures/option_chain_gdx_puts.json"))


def _market(**kw):
    return LiveMarket(["GDX"], set(), OBS,
                      closes_fn=lambda tk: closes_from_json(PH),
                      chain_fn=lambda tk: chain_from_json(OC, OBS), **kw)


def _cfg(**kw):
    base = dict(ticker="GDX", put_delta=0.30, call_delta=0.50, target_dte=11,
                take_profit_pct=0.60, starting_capital=100_000.0,
                call_min_strike="basis")
    base.update(kw)
    return WheelConfig(**base)


def _history(last_iv, *, ticker="GDX", n=252, end=OBS, future=None):
    """A real trailing series ending on `end`: 0.100 -> 0.350 ramp, then
    `last_iv`. Over MIN_RANK_OBS (150) on purpose -- a thinner series returns
    None and a test would pass for the wrong reason.

    `future` appends one observation AFTER `end`, for the look-ahead test.
    """
    days = list(pd.bdate_range(end=end, periods=n))
    vals = [0.100 + i / 1000.0 for i in range(n - 1)] + [last_iv]
    if future is not None:
        days.append(days[-1] + pd.Timedelta(days=1))
        vals.append(future)
    return IVHistory({ticker: pd.Series(vals, index=days)})


# --- the declaration ---------------------------------------------------------

def test_live_market_without_a_history_is_not_rankable():
    assert _market().iv_rankable() is False


def test_live_market_with_a_history_is_rankable():
    assert _market(iv_history=_history(0.40)).iv_rankable() is True


# --- the refusal, from the live entry point ----------------------------------

def test_step_refuses_iv_rank_on_a_live_market_with_no_history():
    """The whole point of the brief. Before this, the same call ran happily and
    ranked every name NEUTRAL."""
    state = PortfolioState(cash=100_000.0, positions=[])
    with pytest.raises(ValueError, match="rank_by='iv_rank'"):
        step_one_day(state, _market(), OBS, _cfg(rank_by="iv_rank"),
                     selector="chop", n_slots=1)


def test_step_runs_iv_rank_on_a_live_market_with_a_history():
    """And the guard must not become an outage: wired properly, it runs."""
    state = PortfolioState(cash=100_000.0, positions=[])
    r = step_one_day(state, _market(iv_history=_history(0.40)), OBS,
                     _cfg(rank_by="iv_rank"), selector="chop", n_slots=1)
    assert isinstance(r.equity, float)
    assert state.prev_d == OBS


# --- the rank itself ---------------------------------------------------------

def test_live_rank_matches_the_history_it_was_given():
    """A rich last observation ranks at the top of its own trailing window."""
    assert _market(iv_history=_history(0.40)).iv_rank("GDX", OBS) == 1.0


def test_live_rank_is_none_for_a_ticker_the_history_does_not_carry():
    """Unknown stays unknown -- absent is not calm. NEUTRAL substitution is
    step_one_day's job and it counts it; the market must not fake a number."""
    assert _market(iv_history=_history(0.40)).iv_rank("NOPE", OBS) is None


def test_live_rank_reads_no_observation_after_the_obs_date():
    """The slice belongs to IVHistory.rank, not the call site (iv_rank.py). If
    LiveMarket ever re-sliced or passed the raw series, a tomorrow-dated
    observation would leak into today's rank -- look-ahead in the live bot."""
    m = _market(iv_history=_history(0.40, future=9.99))
    assert m.iv_rank("GDX", OBS) == 1.0


# --- the plumbing: run_daily's factory must actually pass it through ----------
#
# LiveMarket has accepted `earnings=` since the blackout gate was written, and
# `_live_market()` has never passed it -- which is the whole reason
# earnings_blackout is still not live. The capability existing on the class is
# not the same as the running bot receiving it, and that gap is invisible from
# either side. Both kwargs are tested here so the next one cannot rot the same
# way.

class _DeadClient:
    """Every pull fails. LiveMarket records that in `skipped` and constructs
    anyway, which is what makes this a plumbing test and not a data test."""

    def __getattr__(self, name):
        def _fail(*a, **kw):
            raise RuntimeError("no network in tests")
        return _fail


def _factory(**kw):
    from live.run_daily import _live_market
    return _live_market(["GDX"], set(), OBS, _DeadClient(), 11, {}, **kw)


def test_live_market_factory_passes_the_iv_history_through():
    m = _factory(iv_history=_history(0.40))
    assert m.iv_rankable() is True


def test_live_market_factory_defaults_to_no_iv_history():
    """Default path stays byte-identical: no history, not rankable, and
    step_one_day refuses rather than ranking everything neutral."""
    assert _factory().iv_rankable() is False


def test_live_market_factory_passes_the_earnings_calendar_through():
    class _Cal:
        def dates(self, tk):
            return [pd.Timestamp("2026-07-24")]

    m = _factory(earnings=_Cal())
    assert m.earnings_dates("GDX") == [pd.Timestamp("2026-07-24")]


def test_live_market_factory_defaults_to_no_earnings_calendar():
    assert _factory().earnings_dates("GDX") is None
