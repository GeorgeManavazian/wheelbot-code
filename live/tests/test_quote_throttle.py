"""Every Schwab quote pull must retry a transient 429/502, like every other
Schwab call site in this repo already does.

Why this exists (final review, 2026-08-07). Until the IV accrual pull got its
own systemd timer, everything Schwab-facing ran serialized inside one 5-minute
tick, so two calls could never contend and a 429 was effectively impossible.
Two `OnCalendar=*:0/5` timers CAN overlap, and a 547-ticker chain pull runs at
roughly Schwab's ~120 req/min ceiling on its own.

The failure that opens up is silent, which is the whole point of fixing it:
`contract_quotes` returns {} on a refused pull, `run_intraday` does
`if not quotes: continue`, and a leg sitting at its take-profit is simply not
closed. No ERROR is printed, the process exits 0, so neither the tick's
`grep -qiE "ERROR|Traceback"` nor `OnFailure=wheelbot-alert.service` fires. It
is indistinguishable from "this account holds nothing".

`live/data.py` already had `throttle` and every chain/price-history call used
it. These four quote calls were the only Schwab call sites in the repo that did
not.
"""
import datetime as dt

import live.data as data
from live.held_legs import pull_held_quotes
from live.marks import contract_quotes, live_asks, live_marks

POSITIONS = [{"ticker": "AGNC",
              "short": {"contract": {"root": "AGNC", "expiry": "2026-08-15",
                                     "strike": 11.0, "right": "P"}}}]

# contract_quotes' A7 stale-quote gate refuses any leg whose quote is not
# stamped with today's ET date -- a book whose freshness cannot be judged is not
# tradeable. So the fixture has to be stamped NOW rather than at a fixed instant.
NOW_MS = int(dt.datetime.now(tz=dt.timezone.utc).timestamp() * 1000)

QUOTES = {"AGNC  260815P00011000":
          {"quote": {"mark": 0.15, "bidPrice": 0.10, "askPrice": 0.20,
                     "quoteTimeInLong": NOW_MS}}}


class _Resp:
    """A Schwab-shaped response. A 429 body carries no symbols, which is how the
    silent failure used to happen: .json() succeeds, the lookup misses, and the
    caller returns {} as if the account were empty."""

    def __init__(self, code, payload):
        self.status_code = code
        self._payload = payload

    def json(self):
        return self._payload


class _FlakyClient:
    """429 on the first call, then the real payload. Counts its calls."""

    def __init__(self, payload=QUOTES, fail_times=1):
        self.calls = 0
        self._payload = payload
        self._fail_times = fail_times

    def get_quotes(self, syms):
        self.calls += 1
        if self.calls <= self._fail_times:
            return _Resp(429, {})
        return _Resp(200, self._payload)


def _no_sleep(monkeypatch):
    """throttle sleeps between retries; the test must not."""
    monkeypatch.setattr(data.time, "sleep", lambda *_: None)


def test_live_marks_retries_a_429(monkeypatch):
    _no_sleep(monkeypatch)
    c = _FlakyClient()
    assert live_marks(c, POSITIONS) == {"AGNC": 0.15}
    assert c.calls == 2, "must retry, not return {} on the first 429"


def test_live_asks_retries_a_429(monkeypatch):
    _no_sleep(monkeypatch)
    c = _FlakyClient()
    assert live_asks(c, POSITIONS) == {"AGNC": 0.20}
    assert c.calls == 2


def test_contract_quotes_retries_a_429(monkeypatch):
    _no_sleep(monkeypatch)
    c = _FlakyClient()
    out = contract_quotes(c, POSITIONS)
    assert c.calls == 2
    assert out["AGNC"].ask == 0.20, "the leg the intraday TP check needs"


def test_pull_held_quotes_retries_a_429(monkeypatch):
    _no_sleep(monkeypatch)
    c = _FlakyClient()
    out = pull_held_quotes(c, [POSITIONS], "2026-08-07")
    assert c.calls == 2
    assert out["answered"] == 1


def test_a_persistent_refusal_is_said_out_loud(monkeypatch, capsys):
    """throttle gives up after its retries. The pull still returns {} -- that is
    correct, there are no prices -- but it must no longer be SILENT, or a
    skipped take-profit reads exactly like an empty account.

    The message deliberately contains neither 'error' nor 'Traceback': the tick
    greps for those case-insensitively and would turn a transient quote refusal
    into a failed trading day and an alert page."""
    _no_sleep(monkeypatch)
    c = _FlakyClient(fail_times=99)
    assert contract_quotes(c, POSITIONS) == {}
    said = capsys.readouterr().out
    assert "429" in said and "unpriced" in said
    assert "error" not in said.lower(), "must not trip the tick's ERROR grep"
    assert "traceback" not in said.lower()
