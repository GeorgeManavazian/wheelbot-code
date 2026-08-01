"""Group B data-quality batch (B1/B2/B3/B9 + A22/B7), overnight 2026-08-01.
The selector and regime engine are only as good as their inputs; every fix
here makes a bad input LOUD (raise -> skipped_closes/skipped_chains -> the
zombie gate can judge it) or CLEAN (bad rows dropped), never silent garbage."""
import datetime as dt

import pandas as pd
import pytest

from live.data import closes_from_json, chain_from_json, daily_closes


class _Resp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def json(self):
        return self._p


class _Client:
    def __init__(self, payload):
        self._p = payload

    def get_price_history_every_day(self, tk):
        return _Resp(self._p)


def _candles(closes, start_ms=1753000000000):
    day = 86_400_000
    return {"candles": [{"datetime": start_ms + i * day, "close": c}
                        for i, c in enumerate(closes)]}


def test_b1_nonpositive_and_nonfinite_closes_are_dropped():
    """B1: a 0.0/negative/NaN close row poisons every SMA/regime computation
    downstream (a single 0.0 makes the 9d/20d legs collapse); such rows are
    not prices and must be dropped from the series."""
    s = closes_from_json(_candles([10.0, 0.0, -3.0, float("nan"), 11.0]))
    assert list(s) == [10.0, 11.0], "B1: garbage closes leaked into the series"


def test_b2_empty_payload_is_a_failure_not_a_quiet_series():
    """B2: an empty candles payload used to return an empty Series -- the
    ticker silently vanished from regime consideration and, at scale, an
    outage read as a market holiday. It must RAISE so LiveMarket counts it in
    skipped_closes and the zombie gate can judge the day."""
    with pytest.raises(RuntimeError):
        daily_closes(_Client({"candles": []}), "GDX")
    with pytest.raises(RuntimeError):
        daily_closes(_Client({}), "GDX")


def test_b2_all_garbage_payload_is_also_a_failure():
    # every close invalid -> B1 drops them all -> empty result == failure
    with pytest.raises(RuntimeError):
        daily_closes(_Client(_candles([0.0, -1.0])), "GDX")


def test_b3_one_malformed_contract_does_not_discard_the_ticker():
    """B3: one contract missing strikePrice raised KeyError out of
    chain_from_json, which discarded the ENTIRE ticker for the day
    (skipped_chains). Skip the row, keep the chain."""
    payload = {
        "underlyingPrice": 70.0,
        "putExpDateMap": {"2026-08-07:7": {
            "69.0": [{"strikePrice": 69.0, "daysToExpiration": 7,
                      "delta": -0.3, "bid": 1.0, "ask": 1.1, "mark": 1.05}],
            "68.0": [{"daysToExpiration": 7, "delta": -0.2, "bid": 0.5,
                      "ask": 0.6, "mark": 0.55}],          # no strikePrice
            "67.0": [{"strikePrice": 67.0, "daysToExpiration": "seven",
                      "delta": -0.1, "bid": 0.2, "ask": 0.3, "mark": 0.25}],
        }},
    }
    df = chain_from_json(payload, pd.Timestamp("2026-07-31"))
    assert list(df["strike"]) == [69.0], \
        "B3: the malformed rows must be skipped, the good row kept"


def test_b9_nonpositive_underlying_is_refused():
    """B9: underlyingPrice <= 0 is not a market; every mark/moneyness
    computation downstream divides by or compares against it."""
    for und in (0.0, -1.0):
        with pytest.raises(ValueError):
            chain_from_json({"underlyingPrice": und, "putExpDateMap": {}},
                            pd.Timestamp("2026-07-31"))


def test_a22_b7_request_dates_come_from_eastern_not_the_box():
    """A22/B7: chain_frame stamped from_date/to_date with dt.date.today() --
    the BOX clock. On the UTC VPS any retry from 19:00-20:00 ET onward asks
    Schwab for TOMORROW's expiry window, silently shifting the DTE band the
    selector uses. The request dates must derive from Eastern."""
    import live.data as data
    captured = {}

    class _ChainClient:
        def get_option_chain(self, tk, **kw):
            captured.update(kw)
            return _Resp({"underlyingPrice": 70.0, "putExpDateMap": {}})

    class _FrozenDT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            # 23:30 ET == 03:30 UTC NEXT DAY: the box date is already tomorrow
            base = dt.datetime(2026, 8, 2, 3, 30, tzinfo=dt.timezone.utc)
            return base.astimezone(tz) if tz else base.replace(tzinfo=None)

        @classmethod
        def today(cls):
            return cls(2026, 8, 2, 3, 30)

    class _FrozenDate(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 8, 2)      # the UTC/box date

    import unittest.mock as um
    with um.patch.object(data.dt, "datetime", _FrozenDT), \
         um.patch.object(data.dt, "date", _FrozenDate):
        data.chain_frame(_ChainClient(), "GDX", 11)
    assert captured["from_date"] == dt.date(2026, 8, 1), \
        "A22: the request window must be stamped with the ET date (08-01), " \
        "not the box/UTC date (08-02)"


def test_b10_nonstandard_and_wrong_multiplier_contracts_are_skipped():
    """B10: adjusted/non-standard contracts (splits, special settlement) and
    multiplier != 100 break every cost computation built on x100."""
    base = {"strikePrice": 69.0, "daysToExpiration": 7, "delta": -0.3,
            "bid": 1.0, "ask": 1.1, "mark": 1.05}
    payload = {"underlyingPrice": 70.0, "putExpDateMap": {"2026-08-07:7": {
        "69.0": [dict(base)],
        "68.0": [dict(base, strikePrice=68.0, nonStandard=True)],
        "67.0": [dict(base, strikePrice=67.0, multiplier=10)],
        "66.0": [dict(base, strikePrice=66.0, multiplier=100)],
    }}}
    df = chain_from_json(payload, pd.Timestamp("2026-07-31"))
    assert sorted(df["strike"]) == [66.0, 69.0], \
        "B10: nonStandard/multiplier!=100 rows must be skipped"


def test_b5_held_ticker_outside_universe_is_still_marked():
    """B5: a held ticker that fell out of UNIVERSE had its closes never
    pulled, so its chain was never attempted -- the position froze silently
    (no marks, no TP) forever. Held tickers must be pulled regardless."""
    from live.market_live import LiveMarket
    from live.tests.test_run_daily import PH, OC, OBS
    from live.data import closes_from_json, chain_from_json as cfj
    m = LiveMarket(["SLV"], {"GDX"}, OBS,          # GDX held, NOT in universe
                   closes_fn=lambda tk: closes_from_json(PH),
                   chain_fn=lambda tk: cfj(OC, OBS))
    assert m.chain("GDX", OBS) is not None, \
        "B5: the held-but-retired ticker must still get closes + a chain"


def test_b4_wholesale_held_leg_failure_is_detected():
    """B4: the zombie gate judges closes and chains but NOT held-leg marks --
    a total quote-endpoint failure for the held book printed a line and the
    day completed with every TP suspended. Wholesale failure must be
    detectable as a failed run."""
    from live.run_daily import held_marks_failed
    ok = {"requested": 3, "merged": 2, "unquoted": ["X"], "no_chain": [],
          "error": None, "answered": 3}
    assert held_marks_failed(ok) is False
    assert held_marks_failed({"requested": 0, "merged": 0, "unquoted": [],
                              "no_chain": [], "error": None,
                              "answered": 0}) is False
    # endpoint answered for NOTHING -> wholesale failure -> retry
    assert held_marks_failed({"requested": 3, "merged": 0,
                              "unquoted": ["A", "B", "C"], "no_chain": [],
                              "error": None, "answered": 0}) is True
    assert held_marks_failed({"requested": 3, "merged": 0, "unquoted": [],
                              "no_chain": [], "error": "boom",
                              "answered": 0}) is True
    # group-skeptic F2: a 0.00x0.00 book on every leg is ANSWERED-but-refused
    # -- the endpoint is alive, retrying cannot change the book, and wedging
    # the night over it would suspend all 25 accounts. NOT a failed run.
    assert held_marks_failed({"requested": 2, "merged": 0,
                              "unquoted": ["A", "B"], "no_chain": [],
                              "error": None, "answered": 2}) is False


def test_b3_garbage_expiry_group_kills_only_that_group():
    """Group-skeptic F1: a scribbled expiry KEY used to raise outside the
    per-contract try and discard the whole ticker, good groups included."""
    good = {"strikePrice": 69.0, "daysToExpiration": 7, "delta": -0.3,
            "bid": 1.0, "ask": 1.1, "mark": 1.05}
    payload = {"underlyingPrice": 70.0, "putExpDateMap": {
        "TOTALLY-GARBAGE": {"69.0": [dict(good)]},
        "2026-08-14:14": "not-a-dict",
        "2026-08-07:7": {"69.0": [dict(good)]},
    }}
    df = chain_from_json(payload, pd.Timestamp("2026-07-31"))
    assert list(df["strike"]) == [69.0], \
        "B3: the good expiry group must survive its garbage siblings"
