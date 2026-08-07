"""otm_put_frame: the ONE Schwab call the IV accrual pull makes per ticker.

Two things are load-bearing and neither is obvious from reading the function:

1. The request window must cover EVERY DTE band in ACCRUAL_GRID, not just the
   live one. derived_band(7) = (5, 10) and derived_band(11) = (9, 14), so a
   window sized for target_dte=11 alone yields NO observation at all for every
   DTE-7 grid cell -- the store fills up looking healthy with half the grid
   permanently empty.

2. Schwab sends interestRate/dividendYield as PERCENT (3.707 == 3.707%) while
   implied_vol_put takes decimals. Passing 3.707 does not raise and does not
   warn; it returns a plausible IV that is wrong on every contract forever, in
   a series whose entire purpose is internal consistency. This is the one and
   only place the conversion happens.
"""
import datetime as dt
import json
import unittest.mock as um

import pandas as pd

import live.data as data
from live.data import otm_put_frame, IV_WINDOW_LO, IV_WINDOW_HI, _CHAIN_COLS

PUTS = json.load(open("live/fixtures/option_chain_gdx_puts.json"))
OBS = pd.Timestamp("2026-07-17")


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class _Client:
    """Records the request kwargs and replays the GDX puts fixture."""

    def __init__(self, payload=PUTS):
        self.kw = {}
        self.ticker = None
        self._payload = payload

    def get_option_chain(self, ticker, **kw):
        self.ticker = ticker
        self.kw = kw
        return _Resp(self._payload)


class _FrozenDT(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        base = dt.datetime(2026, 7, 17, 15, 30, tzinfo=dt.timezone.utc)
        return base.astimezone(tz) if tz else base.replace(tzinfo=None)


def _pull(client):
    with um.patch.object(data.dt, "datetime", _FrozenDT):
        return otm_put_frame(client, "GDX", obs_date=OBS)


def test_requests_out_of_the_money_puts_only():
    from schwab.client import Client
    c = _Client()
    _pull(c)
    assert c.ticker == "GDX"
    assert c.kw["contract_type"] is Client.Options.ContractType.PUT
    assert c.kw["strike_range"] is Client.Options.StrikeRange.OUT_OF_THE_MONEY
    assert "strike_count" not in c.kw, "no strike_count guess; OTM range is the point"


def test_window_covers_every_grid_dte_band_not_just_the_live_one():
    """derived_band(7) = (5,10), derived_band(11) = (9,14) -> union 5..14,
    plus one day of slack each side because Schwab's daysToExpiration need not
    agree with (expiry - today).days at the edges."""
    c = _Client()
    _pull(c)
    today = dt.date(2026, 7, 17)
    assert c.kw["from_date"] == today + dt.timedelta(days=IV_WINDOW_LO)
    assert c.kw["to_date"] == today + dt.timedelta(days=IV_WINDOW_HI)
    assert IV_WINDOW_LO <= 4 and IV_WINDOW_HI >= 15


def test_rate_and_div_yield_are_converted_from_percent_to_decimal():
    """The fixture header carries interestRate 3.707 and dividendYield 0.888,
    both PERCENT. Everything downstream must see decimals."""
    df = _pull(_Client())
    assert len(df) > 0
    assert df["rate"].iloc[0] == 0.03707
    assert df["div_yield"].iloc[0] == 0.00888


def test_frame_is_puts_only_and_keeps_the_engine_column_shape():
    df = _pull(_Client())
    assert set(df["right"]) == {"P"}
    assert list(df.columns) == _CHAIN_COLS + ["rate", "div_yield"]
    assert (df["date"] == OBS).all()


def test_missing_header_fields_land_as_none_not_a_crash():
    payload = {k: v for k, v in PUTS.items()
               if k not in ("interestRate", "dividendYield")}
    df = _pull(_Client(payload))
    assert df["rate"].isna().all()
    assert df["div_yield"].isna().all()


def test_non_200_raises_so_the_caller_counts_a_skipped_ticker():
    class _Bad:
        def get_option_chain(self, ticker, **kw):
            r = _Resp({})
            r.status_code = 503
            return r

    import pytest
    with pytest.raises(RuntimeError, match="503"):
        _pull(_Bad())
