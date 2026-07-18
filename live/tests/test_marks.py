from live.marks import occ_symbol, _mark_from_quote, live_marks


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
