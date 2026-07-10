"""Golden-master correctness for the Clenow plugin.

"Correctly implement a known strategy" means: the plugin's decisions match an
INDEPENDENT computation of Clenow's published rules, bar for bar, on real data.
This file reimplements the entry/exit signal separately (vectorised pandas) and
asserts the plugin agrees on every transition over the SPY 2007-2010 fixture.

If the two ever disagree, one is wrong -- that is the point.
"""
import numpy as np
import pandas as pd
import pytest

from src.engine_v2.data.loader import load_bars
from src.engine_v2.strategy.counter_trend import CounterTrendDipBuy, FORECAST_ON

TICKER = "SPY"
DIP = -3.0
CAP = 20


def _reference_signal(close: pd.Series):
    """Clenow ch.17 rules, written independently of the plugin. Returns per-bar
    (bull, pullback). ewm/rolling are causal, so reading position t over the full
    series equals what the plugin computes from history up to t."""
    ema_fast = close.ewm(span=40).mean()
    ema_slow = close.ewm(span=80).mean()
    bull = ema_fast > ema_slow
    std = close.diff().rolling(40).std(ddof=1)          # std of daily CHANGES
    high20 = close.rolling(20).max()
    pullback = (close - high20) / std
    return bull, pullback


def _plugin_positions(bars):
    """Drive the plugin bar-by-bar exactly as the backtest loop does; record whether
    it is long (forecast > 0) at each bar."""
    strat = CounterTrendDipBuy(dip_buy=DIP)
    out = []
    for i in range(len(bars)):
        f = float(strat.forecast(bars.iloc[: i + 1], bars.index[i])[TICKER])
        out.append(f)
    return pd.Series(out, index=bars.index)


@pytest.fixture(scope="module")
def fixture_bars():
    return load_bars([TICKER], "2007-01-01", "2010-12-31")


def test_every_entry_matches_the_independent_signal(fixture_bars):
    """A flat->long transition must occur exactly when bull AND pullback < -3."""
    close = fixture_bars[TICKER]["Close"]
    bull, pullback = _reference_signal(close)
    pos = _plugin_positions(fixture_bars)
    was_flat = pos.shift(1).fillna(0.0) == 0.0
    entries = (pos == FORECAST_ON) & was_flat

    for date in entries[entries].index:
        assert bool(bull.loc[date]), f"entered at {date.date()} but not a bull market"
        assert pullback.loc[date] < DIP, (
            f"entered at {date.date()} on pullback {pullback.loc[date]:.2f} (need < {DIP})"
        )


def test_no_valid_entry_is_missed(fixture_bars):
    """On any bar where the plugin is flat and the signal fires, it MUST enter."""
    close = fixture_bars[TICKER]["Close"]
    bull, pullback = _reference_signal(close)
    pos = _plugin_positions(fixture_bars)
    warmup = max(80, 40, 20) + 1

    for i in range(warmup, len(close)):
        date = close.index[i]
        flat_now = (pos.iloc[i - 1] if i > 0 else 0.0) == 0.0
        signal = bool(bull.iloc[i]) and (pullback.iloc[i] < DIP)
        if flat_now and signal:
            assert pos.iloc[i] == FORECAST_ON, f"missed a valid entry at {date.date()}"


def test_every_exit_is_a_trend_flip_or_the_time_cap(fixture_bars):
    """A long->flat transition must be justified: either the trend turned bearish,
    or the position had been held for the 20-bar cap."""
    close = fixture_bars[TICKER]["Close"]
    bull, _ = _reference_signal(close)
    pos = _plugin_positions(fixture_bars)

    held = 0
    for i in range(len(pos)):
        date = pos.index[i]
        if pos.iloc[i] == FORECAST_ON:
            held += 1
        else:
            if i > 0 and pos.iloc[i - 1] == FORECAST_ON:  # just exited
                trend_flip = not bool(bull.loc[date])
                hit_cap = held >= CAP
                assert trend_flip or hit_cap, (
                    f"exited at {date.date()} after {held} bars with no reason "
                    f"(bull={bool(bull.loc[date])}, cap={CAP})"
                )
            held = 0


def test_it_actually_traded_on_the_fixture(fixture_bars):
    """A golden master that never fires proves nothing. Clenow fires rarely, but it
    must fire at least once here, or the checks above are vacuous."""
    pos = _plugin_positions(fixture_bars)
    entries = ((pos == FORECAST_ON) & (pos.shift(1).fillna(0.0) == 0.0)).sum()
    assert entries >= 1, "plugin never entered on the fixture -- golden master is vacuous"


def test_holds_never_exceed_the_cap(fixture_bars):
    """No position may persist beyond 20 bars -- Clenow's hard time exit."""
    pos = _plugin_positions(fixture_bars)
    runs = (pos == FORECAST_ON).astype(int)
    grp = (runs == 0).cumsum()
    longest = runs.groupby(grp).sum().max()
    assert longest <= CAP, f"held {longest} bars, cap is {CAP}"
