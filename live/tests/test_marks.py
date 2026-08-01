from live.marks import occ_symbol, _mark_from_quote, live_marks, live_asks, contract_quotes


def test_occ_symbol_format():
    assert occ_symbol("AGNC", "2026-08-15", 11.0, "P") == "AGNC  260815P00011000"
    assert occ_symbol("BA", "2026-08-01", 205.0, "P") == "BA    260801P00205000"
    assert occ_symbol("spy", "2026-12-18", 612.5, "C") == "SPY   261218C00612500"


def test_mark_prefers_mark_then_mid_then_last():
    assert _mark_from_quote({"quote": {"mark": 1.25, "bidPrice": 1.2, "askPrice": 1.4}}) == 1.25
    assert _mark_from_quote({"quote": {"bidPrice": 1.0, "askPrice": 2.0}}) == 1.5
    assert _mark_from_quote({"quote": {"lastPrice": 0.9}}) == 0.9
    assert _mark_from_quote({"quote": {}}) is None


class _FakeResp:
    def __init__(self, d): self._d = d
    def json(self): return self._d


class _FakeClient:
    def __init__(self, d): self._d = d
    def get_quotes(self, syms): return _FakeResp(self._d)


def test_live_marks_maps_ticker_to_mark():
    positions = [
        {"ticker": "AGNC", "short": {"contract": {"root": "AGNC", "expiry": "2026-08-15",
                                                   "strike": 11.0, "right": "P"}}},
        {"ticker": "BA", "short": {"contract": {"root": "BA", "expiry": "2026-08-01",
                                                "strike": 205.0, "right": "P"}}},
        {"ticker": "XYZ", "short": None},  # no leg -> skipped
    ]
    quotes = {
        "AGNC  260815P00011000": {"quote": {"mark": 0.15}},
        "BA    260801P00205000": {"quote": {"bidPrice": 3.0, "askPrice": 3.6}},
    }
    out = live_marks(_FakeClient(quotes), positions)
    assert out == {"AGNC": 0.15, "BA": 3.3}


def test_live_marks_empty_on_failure():
    class Boom:
        def get_quotes(self, syms): raise RuntimeError("no token")
    positions = [{"ticker": "AGNC", "short": {"contract": {"root": "AGNC",
                 "expiry": "2026-08-15", "strike": 11.0, "right": "P"}}}]
    assert live_marks(Boom(), positions) == {}
    assert live_marks(_FakeClient({}), []) == {}




def _fresh_ms():
    # A7: contract_quotes now refuses quotes not stamped with today's ET date;
    # admission-behavior tests need a fresh stamp on their fixture books
    import time
    return int(time.time() * 1000)

def test_contract_quotes_returns_bid_ask_mark():
    positions = [{"ticker": "AGNC", "short": {"contract": {"root": "AGNC",
                 "expiry": "2026-08-15", "strike": 11.0, "right": "P"}}}]
    quotes = {"AGNC  260815P00011000": {"quote": {"bidPrice": 0.38, "askPrice": 0.40, "quoteTimeInLong": _fresh_ms()}}}
    out = contract_quotes(_FakeClient(quotes), positions)
    m = out["AGNC"]
    assert (m.bid, m.ask) == (0.38, 0.40)
    assert abs(m.mid - 0.39) < 1e-9          # midpoint when no exchange mark
    # missing bid/ask -> skipped
    assert contract_quotes(_FakeClient({"AGNC  260815P00011000": {"quote": {}}}), positions) == {}


def test_contract_quotes_skips_zero_quote():
    # C1 regression: a 0/0 two-sided quote (halt / pre-open / thin option) must be
    # SKIPPED, not returned as Mark(0,0,0) — else the intraday TP check closes for free.
    positions = [{"ticker": "AGNC", "short": {"contract": {"root": "AGNC",
                 "expiry": "2026-08-15", "strike": 11.0, "right": "P"}}}]
    sym = "AGNC  260815P00011000"
    assert contract_quotes(_FakeClient({sym: {"quote": {"bidPrice": 0.0, "askPrice": 0.0, "quoteTimeInLong": _fresh_ms()}}}), positions) == {}
    # NOTE: a one-sided zero (bid 0, ask 0.05) was asserted skipped here until A6
    # (audit 2026-07-31). That was the defect, not the spec: it made the intraday
    # manager blind to a fully-decayed leg. It is now REQUIRED to come through --
    # see test_a_worthless_leg_is_still_closable_intraday below.
    # a real two-sided quote still comes through
    out = contract_quotes(_FakeClient({sym: {"quote": {"bidPrice": 0.10, "askPrice": 0.12, "quoteTimeInLong": _fresh_ms()}}}), positions)
    assert out["AGNC"].ask == 0.12


def test_live_asks_returns_the_offer_and_keeps_a_zero_bid():
    """What it costs to CLOSE the short book. A 0.00 x 0.01 leg is worthless and
    must still be shown as worthless, so only the ask is required to be real."""
    positions = [{"ticker": "RIG", "short": {"contract": {"root": "RIG",
                 "expiry": "2026-08-07", "strike": 4.5, "right": "P"}}}]
    quotes = {"RIG   260807P00004500": {"quote": {"bidPrice": 0.0, "askPrice": 0.01}}}
    assert live_asks(_FakeClient(quotes), positions) == {"RIG": 0.01}


def test_live_asks_skips_a_zero_ask():
    """ask=0 is a halt or an empty book, not a price -- and it would display the
    liability as nil."""
    positions = [{"ticker": "RIG", "short": {"contract": {"root": "RIG",
                 "expiry": "2026-08-07", "strike": 4.5, "right": "P"}}}]
    quotes = {"RIG   260807P00004500": {"quote": {"bidPrice": 0.0, "askPrice": 0.0}}}
    assert live_asks(_FakeClient(quotes), positions) == {}


def test_a_worthless_leg_is_still_closable_intraday():
    """A6 (audit 2026-07-31). `contract_quotes` required bid>0 AND ask>0, so a
    fully-decayed put quoted 0.00 x 0.01 -- the exact state a winning position
    ends in -- was invisible to the intraday manager and could never be taken
    off. The sibling EOD path (`held_legs.rows_from_quotes`) was fixed for this
    on the same day and requires only a real ASK; the two functions disagreed
    about the same contract.

    Measured: 45% of the bot's own 0.30-delta/11-DTE picks decay to ask <= $0.05
    before expiry (DOW 31%, WMT 48%, AGNC 84%), and the intraday manager
    produces 100% of realized P&L -- so this is the path that matters."""
    positions = [{"ticker": "RIG", "short": {"contract": {
        "root": "RIG", "expiry": "2026-08-07", "strike": 4.5, "right": "P"}}}]
    quotes = {"RIG   260807P00004500": {"quote": {"bidPrice": 0.0, "askPrice": 0.01,
                                                  "quoteTimeInLong": _fresh_ms()}}}
    out = contract_quotes(_FakeClient(quotes), positions)
    assert "RIG" in out, "a 0.00 x 0.01 market is worthless, not absent"
    assert (out["RIG"].bid, out["RIG"].ask) == (0.0, 0.01)


def test_contract_quotes_still_refuses_a_two_sided_zero():
    """The protection that must survive the fix. A 0.00 x 0.00 quote is a halt,
    a pre-open book or a dead strike -- not a price. An ask of zero satisfies
    `ask <= (1-TP)*credit` for ANY credit, so accepting it would close the leg
    for free and drop it. That is defect C1 from the 2026-07-18 audit."""
    positions = [{"ticker": "RIG", "short": {"contract": {
        "root": "RIG", "expiry": "2026-08-07", "strike": 4.5, "right": "P"}}}]
    quotes = {"RIG   260807P00004500": {"quote": {"bidPrice": 0.0, "askPrice": 0.0, "quoteTimeInLong": _fresh_ms()}}}
    assert contract_quotes(_FakeClient(quotes), positions) == {}


def test_contract_quotes_survives_a_non_numeric_quote():
    """Regression introduced BY the A6 clause reorder and caught by the skeptic
    pass. Testing `ask <= 0` before `bid <= 0` removed the short-circuit that used
    to swallow a non-numeric ask, so a single malformed leg raised TypeError out
    of `run_intraday`'s per-account try/except (run_intraday.py:66) and suppressed
    take-profit for every OTHER position in that account for that tick.

    `_num` is the fix: same coercion `held_legs.rows_from_quotes` already used."""
    positions = [{"ticker": "RIG", "short": {"contract": {
        "root": "RIG", "expiry": "2026-08-07", "strike": 4.5, "right": "P"}}}]
    # NB "0.05" is NOT in this list: a numeric string coerces, matching
    # rows_from_quotes. Only genuinely unusable values are refused.
    for bad in ("", None, "abc", float("nan"), {}, [], "None"):
        quotes = {"RIG   260807P00004500": {"quote": {"bidPrice": 0.0, "askPrice": bad,
                                                      "quoteTimeInLong": _fresh_ms()}}}
        out = contract_quotes(_FakeClient(quotes), positions)   # must not raise
        assert out == {}, f"askPrice={bad!r} must be refused, got {out}"
    # and a NUMERIC-STRING bid with a real ask is coerced, not refused
    quotes = {"RIG   260807P00004500": {"quote": {"bidPrice": "0.00", "askPrice": "0.05",
                                                  "quoteTimeInLong": _fresh_ms()}}}
    out = contract_quotes(_FakeClient(quotes), positions)
    assert (out["RIG"].bid, out["RIG"].ask, out["RIG"].mid) == (0.0, 0.05, 0.025)


def test_contract_quotes_refuses_a_nan_bid():
    """A NaN bid was admitted BOTH before and after the A6 reorder -- `nan <= 0`
    and `nan < 0` are both False, so every ordering of the raw comparison lets it
    through. (The NaN *ask* case, covered above, is the one this diff regressed:
    the old `bid <= 0` clause used to swallow it by accident.) Harmless
    downstream, but it made `contract_quotes` and `rows_from_quotes` disagree
    about the same contract, which is the exact failure A6 exists to end."""
    positions = [{"ticker": "RIG", "short": {"contract": {
        "root": "RIG", "expiry": "2026-08-07", "strike": 4.5, "right": "P"}}}]
    quotes = {"RIG   260807P00004500": {"quote": {"bidPrice": float("nan"), "askPrice": 0.05, "quoteTimeInLong": _fresh_ms()}}}
    assert contract_quotes(_FakeClient(quotes), positions) == {}


def test_contract_quotes_refuses_a_negative_bid():
    """Not a price either. Keep this in agreement with held_legs.rows_from_quotes,
    which uses `bid < 0` for the same purpose."""
    positions = [{"ticker": "RIG", "short": {"contract": {
        "root": "RIG", "expiry": "2026-08-07", "strike": 4.5, "right": "P"}}}]
    quotes = {"RIG   260807P00004500": {"quote": {"bidPrice": -0.01, "askPrice": 0.05, "quoteTimeInLong": _fresh_ms()}}}
    assert contract_quotes(_FakeClient(quotes), positions) == {}


def _q_with_time(bid, ask, quote_ms):
    return {"GDX   260821P00030000": {"quote": {"bidPrice": bid, "askPrice": ask,
                                                "quoteTimeInLong": quote_ms}}}


def _pos_gdx():
    from src.engine_v2.options.chain import Contract
    import pandas as pd
    return [{"ticker": "GDX",
             "short": {"contract": Contract("GDX", pd.Timestamp("2026-08-21"),
                                            30.0, "P"), "contracts": 1,
                       "credit": 1.0, "last_mid": 1.0}}]


class _RawClient:
    # returns a raw dict (no .json()) -- exercises marks.py's hasattr branch's
    # OTHER arm; the original _FakeClient at the top of this file keeps the
    # .json() arm covered (skeptic F3: this class previously SHADOWED it)
    def __init__(self, payload):
        self._p = payload

    def get_quotes(self, syms):
        return self._p


def test_prior_session_quote_is_refused():
    """A7: on a market holiday the exchange never opens, but Schwab still
    serves the LAST session's book -- market_is_open passes (it knows no
    holidays by design) and the bot booked CLOSE_PUT @ 0.39 on Thanksgiving.
    A quote stamped on a previous session's ET date is not a price you can
    trade on today; refuse it like an absent quote (TP waits for the EOD
    run). Parameter-free: no vendored holiday calendar to rot."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    from live.marks import contract_quotes
    stale_ms = int(dt.datetime(2026, 7, 30, 15, 59,
                               tzinfo=ZoneInfo("America/New_York"))
                   .timestamp() * 1000)          # a past session, always stale
    q = contract_quotes(_RawClient(_q_with_time(0.30, 0.39, stale_ms)),
                        _pos_gdx())
    assert q == {}, "A7: a prior-session quote must be refused, not traded on"


def test_same_session_quote_is_served():
    import time
    from live.marks import contract_quotes
    q = contract_quotes(_RawClient(_q_with_time(0.30, 0.39,
                                                int(time.time() * 1000))),
                        _pos_gdx())
    assert "GDX" in q and q["GDX"].ask == 0.39


def test_missing_quote_timestamp_is_refused():
    # fail-safe: a book whose freshness cannot be judged is not tradeable
    # (provisional; Schwab always stamps quoteTimeInLong in practice)
    from live.marks import contract_quotes
    q = contract_quotes(_RawClient(
        {"GDX   260821P00030000": {"quote": {"bidPrice": 0.30,
                                             "askPrice": 0.39}}}),
        _pos_gdx())
    assert q == {}


def test_pathological_timestamp_refuses_the_leg_not_the_account():
    """Skeptic F1: inf / out-of-range / unit-drifted quoteTimeInLong must
    refuse THIS leg, never raise out of the whole account's tick (vendor unit
    drift is exactly the failure class this gate exists for)."""
    for crazy in (float("inf"), 1e16, -9e15):
        q = contract_quotes(_RawClient(_q_with_time(0.30, 0.39, crazy)),
                            _pos_gdx())
        assert q == {}, f"quoteTimeInLong={crazy} must refuse, not raise"
