"""E7 kills for audit surviving mutations #4-#9 (2026-08-01 session 2).

E7 re-ran all nine mutations against the then-current 293+282 suite:
#1-#3 (equity arithmetic) die on the E1/E2 pins; #4-#9 (held_legs quote
admission, dedup, dte, unquoted stats, spliced delta; dashboard live-ask
path) still survived every test. One value-asserting test per mutation."""
import pandas as pd

from live.held_legs import held_contracts, rows_from_quotes
from live.marks import live_asks, occ_symbol

OBS = pd.Timestamp("2026-07-21")
EXP = pd.Timestamp("2026-08-07")


def _contract(ticker="RIG", strike=4.5, right="P", expiry=EXP):
    return {"ticker": ticker, "root": ticker, "expiry": expiry,
            "strike": strike, "right": right,
            "symbol": occ_symbol(ticker, expiry, strike, right)}


def _payload(sym, bid=0.03, ask=0.05, delta=-0.12, dte=17, mark=None):
    quote = {"bidPrice": bid, "askPrice": ask, "underlyingPrice": 5.30,
             "openInterest": 100, "totalVolume": 10, "bidSize": 5,
             "askSize": 5}
    if delta is not None:
        quote["delta"] = delta
    if mark is not None:
        quote["mark"] = mark
    return {sym: {"quote": quote, "reference": {"daysToExpiration": dte}}}


def test_mutation4_negative_bid_is_refused():
    """#4: `bid < 0` deleted survived. A negative bid is a corrupt or crossed
    book, not a price; the row must not be built."""
    c = _contract()
    rows = rows_from_quotes(_payload(c["symbol"], bid=-0.01, ask=0.05), [c], OBS)
    assert rows == {}, "audit mutation #4: a negative-bid quote built a row"


def test_mutation5_schwab_dte_is_used_not_recomputed():
    """#5: ignoring Schwab's daysToExpiration survived. When the reference
    node carries a dte it is the exchange's own number and must win over the
    date arithmetic (they differ across half-days/settlement conventions)."""
    c = _contract()
    rows = rows_from_quotes(_payload(c["symbol"], dte=99), [c], OBS)
    assert rows["RIG"][0]["dte"] == 99, \
        "audit mutation #5: Schwab's daysToExpiration was thrown away"
    # and the fallback still works when the field is absent
    p = _payload(c["symbol"])
    p[c["symbol"]]["reference"] = {}
    rows = rows_from_quotes(p, [c], OBS)
    assert rows["RIG"][0]["dte"] == (EXP - OBS).days


def test_mutation6_unanswered_leg_is_named_in_unquoted():
    """#6: hardcoding stats['unquoted'] = [] survived. The unquoted list is
    the ONLY disclosure that a held leg got no mark today (its TP is
    silently suspended) -- B4's wholesale gate and the run log both read it."""
    from live.held_legs import merge_held_legs

    class _Mkt:
        def __init__(self):
            self._chains = {}

        def chain(self, tk, d):
            return self._chains.get(tk)

    class _Client:
        def get_quotes(self, syms):
            class R:
                def json(self):
                    return {}          # endpoint answers, names nothing
            return R()

    c = _contract()
    positions = [[{"ticker": "RIG", "shares": 0, "phase": "PUT",
                   "basis": None, "premium": 0.0, "campaign": 1,
                   "last_spot": 5.3,
                   "short": {"contract": {"root": "RIG", "expiry": str(EXP),
                                          "strike": 4.5, "right": "P"},
                             "contracts": 8, "credit": 0.03,
                             "last_mid": 0.02}}]]
    stats = merge_held_legs(_Mkt(), _Client(), positions, OBS)
    assert stats["unquoted"] == [c["symbol"]], \
        "audit mutation #6: an unanswered held leg vanished from unquoted"


def test_mutation7_dedup_key_keeps_distinct_legs_per_ticker():
    """#7: reducing the dedup key to (ticker,) survived. Two accounts holding
    DIFFERENT strikes of the same ticker must yield two contracts -- the
    collapsed key quotes one and silently never marks the other."""
    def pos(strike):
        return [{"ticker": "RIG", "short": {"contract": {
            "root": "RIG", "expiry": str(EXP), "strike": strike,
            "right": "P"}}}]
    got = held_contracts([pos(4.5), pos(5.0)])
    assert len(got) == 2, \
        "audit mutation #7: distinct strikes collapsed to one quote request"
    assert {c["strike"] for c in got} == {4.5, 5.0}
    # and the true-duplicate case still dedupes to one
    assert len(held_contracts([pos(4.5), pos(4.5)])) == 1


def test_mutation8_spliced_row_carries_the_quotes_delta():
    """#8: hardcoding delta None on every spliced row survived. The delta
    feeds the A4 splice sanity guard and any selector that ever sees the row;
    the quote names it and it must be carried."""
    c = _contract()
    rows = rows_from_quotes(_payload(c["symbol"], delta=-0.12), [c], OBS)
    assert rows["RIG"][0]["delta"] == -0.12, \
        "audit mutation #8: the quote's delta was replaced with None"


def test_mutation9_dashboard_live_pull_returns_ask_not_mid():
    """#9 (E3): reverting the dashboard live pull from ask to mid survived --
    the live-quote path had zero tests. Owner decision B (2026-07-29): a
    short is a liability priced at what it COSTS TO CLOSE, the ask; the mid
    books half a spread the account can never capture."""
    sym = occ_symbol("RIG", EXP, 4.5, "P")

    class _Client:
        def get_quotes(self, syms):
            class R:
                def json(self):
                    return {sym: {"quote": {"mark": 0.03, "bidPrice": 0.01,
                                            "askPrice": 0.10}}}
            return R()

    positions = [{"ticker": "RIG",
                  "short": {"contract": {"root": "RIG", "expiry": str(EXP),
                                         "strike": 4.5, "right": "P"}}}]
    out = live_asks(_Client(), positions)
    assert out == {"RIG": 0.10}, \
        "audit mutation #9: the dashboard priced the book at mid, not ask"


# ---- A10 intraday refusals ----

def test_intraday_quote_with_split_shaped_underlying_gap_is_refused(capsys):
    """A10: split morning, EOD detection has not run yet -- the 10:00 manager
    would book a phantom close against pre-split state. A quote whose OWN
    underlyingPrice gaps >=25% from the stored last_spot is refused like an
    absent quote (the A7 degrade), loudly."""
    from live.marks import contract_quotes
    sym = occ_symbol("XYZ", EXP, 100.0, "P")

    class _Client:
        def get_quotes(self, syms):
            class R:
                def json(self):
                    import time
                    return {sym: {"quote": {"bidPrice": 0.01, "askPrice": 0.05,
                                            "underlyingPrice": 52.0,
                                            "quoteTimeInLong": int(time.time() * 1000)}}}
            return R()

    positions = [{"ticker": "XYZ", "last_spot": 104.0,
                  "short": {"contract": {"root": "XYZ", "expiry": str(EXP),
                                         "strike": 100.0, "right": "P"}}}]
    out = contract_quotes(_Client(), positions)
    assert "XYZ" not in out, \
        "A10: intraday manager accepted a quote across a 50% underlying gap"
    assert "corporate-action" in capsys.readouterr().out.lower()


def test_intraday_quote_with_normal_underlying_passes():
    from live.marks import contract_quotes
    sym = occ_symbol("XYZ", EXP, 100.0, "P")

    class _Client:
        def get_quotes(self, syms):
            class R:
                def json(self):
                    import time
                    return {sym: {"quote": {"bidPrice": 0.01, "askPrice": 0.05,
                                            "underlyingPrice": 103.0,
                                            "quoteTimeInLong": int(time.time() * 1000)}}}
            return R()

    positions = [{"ticker": "XYZ", "last_spot": 104.0,
                  "short": {"contract": {"root": "XYZ", "expiry": str(EXP),
                                         "strike": 100.0, "right": "P"}}}]
    out = contract_quotes(_Client(), positions)
    assert "XYZ" in out


def test_intraday_manager_skips_frozen_positions():
    """A10: a ca_frozen position must not TP intraday either -- the freeze
    means every stored number is in unknown units."""
    from src.engine_v2.options.portfolio import PortfolioState
    from src.engine_v2.options.select import Contract
    from live.intraday import manage_intraday
    from live.marks import Mark
    from src.engine_v2.options.wheel import WheelConfig
    exp = EXP
    pos = {"ticker": "XYZ", "shares": 0, "phase": "PUT", "basis": None,
           "premium": 0.0, "campaign": 1, "last_spot": 104.0,
           "ca_frozen": {"date": "2026-08-01", "stored_spot": 104.0,
                         "ratio": 0.5},
           "short": {"contract": Contract("XYZ", exp, 100.0, "P"),
                     "contracts": 9, "credit": 2.00, "last_mid": 2.0}}
    st = PortfolioState(cash=10_000.0, positions=[pos])
    cfg = WheelConfig(ticker="XYZ", starting_capital=100_000.0,
                      put_delta=0.30, call_delta=0.50, target_dte=11,
                      take_profit_pct=0.60, call_min_strike="basis")
    trades = manage_intraday(st, {"XYZ": Mark(0.01, 0.05, 0.03)}, cfg,
                             pd.Timestamp("2026-07-21 10:00"))
    assert trades == [], "A10: the intraday manager closed a frozen leg"
    assert pos["short"] is not None
