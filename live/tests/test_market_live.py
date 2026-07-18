import json
import pandas as pd
from live.data import closes_from_json, chain_from_json
from live.market_live import LiveMarket

OBS = pd.Timestamp("2026-07-17")
PH = json.load(open("live/fixtures/price_history_gdx.json"))
OC = json.load(open("live/fixtures/option_chain_gdx_puts.json"))


def _closes_fn(tk):
    # every ticker gets the GDX close history (fine for a mapping test)
    return closes_from_json(PH)


def _chain_fn(tk):
    return chain_from_json(OC, OBS)


def _mkt(held=()):
    return LiveMarket(["GDX", "AAA"], set(held), OBS,
                      closes_fn=_closes_fn, chain_fn=_chain_fn)


def test_spot_is_today_close_or_fallback():
    m = _mkt()
    assert m.spot("GDX", OBS, 0.0) == 71.32          # last fixture close
    assert m.spot("GDX", pd.Timestamp("2099-01-01"), 5.0) == 5.0


def test_regime_row_is_prior_day():
    m = _mkt()
    row = m.regime_row("GDX", OBS)
    assert row is not None and "trend" in row


def test_settle_price_on_or_before_expiry_and_eligible_true():
    m = _mkt()
    # last close on-or-before OBS (the fixture's last date) is today's close 71.32
    assert m.settle_price("GDX", OBS) == 71.32
    # before any close exists -> None
    assert m.settle_price("GDX", pd.Timestamp("2000-01-01")) is None
    assert m.eligible("GDX", OBS) is True


def test_universe_property():
    assert _mkt().universe == ["GDX", "AAA"]


def test_held_ticker_chain_present():
    # a held ticker always gets its chain (needed for management), even if not
    # good-to-rent
    m = _mkt(held=["GDX"])
    assert m.chain("GDX", OBS) is not None
    assert list(m.chain("GDX", OBS).columns)[:3] == ["date", "expiry", "strike"]


def test_pull_failure_skips_ticker():
    def flaky(tk):
        if tk == "BAD":
            raise RuntimeError("BAD price_history -> HTTP 404")
        return closes_from_json(PH)
    m = LiveMarket(["GDX", "BAD"], set(), OBS, closes_fn=flaky, chain_fn=_chain_fn)
    assert "BAD" not in m._closes          # skipped, not fatal
    assert m.spot("GDX", OBS, 0.0) == 71.32
