"""The take-profit silently stopped applying to any position whose strike drifted
outside the `strike_count=12` chain window (audit 2026-07-29, finding 1).

Confirmed live: TMO 512.5P was carried at $11.30 — its mark from the day it was
sold — while the stock ran to 576, making the contract worth about $0.42. It was
far past its 60% take-profit trigger and was never closed, because `option_mark`
found no row for that strike in the bounded chain and returned None, and every
branch that could act on the leg is guarded by `mark is not None`.
"""
import pandas as pd

from live.data import _CHAIN_COLS
from live.held_legs import held_contracts, rows_from_quotes, merge_held_legs
from live.market_live import LiveMarket
from src.engine_v2.options.select import option_mark
from src.engine_v2.options.chain import Contract
from src.engine_v2.options.portfolio import PortfolioState, step_one_day
from src.engine_v2.options.wheel import WheelConfig

OBS = pd.Timestamp("2026-07-31")
EXP = pd.Timestamp("2026-08-07")


def _quote(bid, ask, mark=None, delta=-0.05, dte=7, und=576.77,
           oi=317, vol=12, bsz=5, asz=9):
    # liquidity fields sit at the QUOTE node in Schwab's QuoteResponse (A19);
    # carrying them here lets the value assertions below catch a renamed or
    # relocated key, which would otherwise record None forever with green tests
    q = {"quote": {"bidPrice": bid, "askPrice": ask, "delta": delta,
                   "underlyingPrice": und, "openInterest": oi,
                   "totalVolume": vol, "bidSize": bsz, "askSize": asz},
         "reference": {"daysToExpiration": dte}}
    if mark is not None:
        q["quote"]["mark"] = mark
    return q


def _position(ticker="TMO", strike=512.5, right="P", credit=8.0, contracts=1,
              expiry=EXP, last_mid=11.30):
    return {"ticker": ticker, "shares": 0, "phase": "PUT", "basis": None,
            "premium": credit * 100 * contracts, "campaign": 1, "last_spot": 576.77,
            "short": {"contract": Contract(ticker, expiry, strike, right),
                      "contracts": contracts, "credit": credit, "last_mid": last_mid}}


def test_held_contracts_dedupes_the_same_leg_across_accounts():
    """25 accounts hold overlapping legs; the quote pull must ask once."""
    a, b = [_position()], [_position(), _position(ticker="RIG", strike=4.5)]
    out = held_contracts([a, b])
    assert len(out) == 2
    assert {c["ticker"] for c in out} == {"TMO", "RIG"}


def test_row_captures_quote_times_dual_key():
    """C1/C1b: the quotes endpoint's spec-shaped key is `quoteTime`; older
    captures used `quoteTimeInLong`. Capture whichever is present (never
    both invented); absent -> None and the row is STILL kept (a held row
    must keep splicing -- flag-only, never a drop)."""
    contracts = held_contracts([[_position()]])
    q1 = _quote(0.40, 0.44, mark=0.42)
    q1["quote"]["quoteTime"] = 1754140000000
    q1["quote"]["tradeTime"] = 1754139000000
    r = rows_from_quotes({"TMO   260807P00512500": q1}, contracts, OBS)["TMO"][0]
    assert r["quote_time"] == 1754140000000.0
    assert r["trade_time"] == 1754139000000.0
    q2 = _quote(0.40, 0.44, mark=0.42)
    q2["quote"]["quoteTimeInLong"] = 1754141111000
    r2 = rows_from_quotes({"TMO   260807P00512500": q2}, contracts, OBS)["TMO"][0]
    assert r2["quote_time"] == 1754141111000.0
    q3 = _quote(0.40, 0.44, mark=0.42)   # neither key
    r3 = rows_from_quotes({"TMO   260807P00512500": q3}, contracts, OBS)["TMO"][0]
    assert r3["quote_time"] is None and r3["trade_time"] is None, \
        "C1: a held row with no timestamp must still splice (flag-only)"


def test_row_built_for_a_leg_outside_the_chain_window():
    contracts = held_contracts([[_position()]])
    rows = rows_from_quotes({"TMO   260807P00512500": _quote(0.40, 0.44, mark=0.42)},
                            contracts, OBS)
    assert list(rows) == ["TMO"]
    r = rows["TMO"][0]
    # every chain column, plus the mark-only flag that keeps it out of selection
    assert set(r) == set(_CHAIN_COLS) | {"held_only"}
    assert r["held_only"] is True
    assert r["date"] == OBS and r["expiry"] == EXP
    assert (r["strike"], r["right"]) == (512.5, "P")
    assert (r["bid"], r["ask"], r["mid"]) == (0.40, 0.44, 0.42)
    # A19 skeptic F5: values, not just column presence -- a wrong/moved key
    # in rows_from_quotes would record None forever while presence stays green
    assert (r["open_interest"], r["volume"]) == (317.0, 12.0)
    assert (r["bid_size"], r["ask_size"]) == (5.0, 9.0)
    assert r["underlying"] == 576.77 and r["dte"] == 7


def test_zero_bid_with_a_penny_ask_still_marks():
    """RIG 4.5P quoted 0.00 x 0.01 — worthless, but that IS the price, and it is
    exactly the leg the take-profit should be buying back. The EOD chain builder
    drops bid<=0 (right, for a contract you might SELL); a held leg is different."""
    contracts = held_contracts([[_position(ticker="RIG", strike=4.5, credit=0.03)]])
    rows = rows_from_quotes({"RIG   260807P00004500": _quote(0.0, 0.01)},
                            contracts, OBS)
    assert rows["RIG"][0]["ask"] == 0.01
    assert rows["RIG"][0]["bid"] == 0.0


def test_two_sided_zero_quote_is_skipped():
    """0.00 x 0.00 is a halt/pre-open/no-market quote, not a price. Marking it
    would make ask=0 satisfy any take-profit test and 'close' the leg for free
    — the same defect C1 that was fixed in contract_quotes on 2026-07-18."""
    contracts = held_contracts([[_position()]])
    assert rows_from_quotes({"TMO   260807P00512500": _quote(0.0, 0.0)},
                            contracts, OBS) == {}


def test_quote_missing_entirely_is_skipped_not_guessed():
    contracts = held_contracts([[_position()]])
    assert rows_from_quotes({}, contracts, OBS) == {}


class _FakeResp:
    def __init__(self, d): self._d = d
    def json(self): return self._d


class _FakeClient:
    def __init__(self, d): self._d = d
    def get_quotes(self, syms): return _FakeResp(self._d)


def _chain(strikes, obs=OBS, expiry=EXP, und=576.77, right="P"):
    return pd.DataFrame(
        [{"date": obs, "expiry": expiry, "strike": s, "right": right,
          "dte": (expiry - obs).days, "delta": -0.30, "bid": 5.0, "ask": 5.2,
          "mid": 5.1, "underlying": und} for s in strikes],
        columns=_CHAIN_COLS)


def _market(chain, closes_last=576.77):
    closes = pd.Series([closes_last] * 300,
                       index=pd.bdate_range(end=OBS, periods=300))
    return LiveMarket(["TMO"], {"TMO"}, OBS,
                      closes_fn=lambda tk: closes,
                      chain_fn=lambda tk: chain)


def test_merge_adds_the_missing_leg_so_option_mark_finds_it():
    market = _market(_chain([560.0, 570.0, 580.0]))
    contract = Contract("TMO", EXP, 512.5, "P")
    assert option_mark(market.chain("TMO", OBS), OBS, contract) is None   # the bug

    client = _FakeClient({"TMO   260807P00512500": _quote(0.40, 0.44, mark=0.42)})
    stats = merge_held_legs(market, client, [[_position()]], OBS)

    mk = option_mark(market.chain("TMO", OBS), OBS, contract)
    assert mk is not None and mk.ask == 0.44
    assert stats["merged"] == 1 and stats["requested"] == 1


def test_merge_leaves_a_leg_already_in_the_chain_alone():
    """The EOD chain is the authoritative snapshot; a quote pulled seconds later
    must not silently restate a strike the chain already covers."""
    market = _market(_chain([512.5, 570.0]))
    client = _FakeClient({"TMO   260807P00512500": _quote(0.40, 0.44, mark=0.42)})
    stats = merge_held_legs(market, client, [[_position()]], OBS)

    mk = option_mark(market.chain("TMO", OBS), OBS, Contract("TMO", EXP, 512.5, "P"))
    assert mk.ask == 5.2          # the chain's row, untouched
    assert stats["merged"] == 0
    assert len(market.chain("TMO", OBS)) == 2


def test_merge_survives_a_dead_quote_pull():
    class Boom:
        def get_quotes(self, syms): raise RuntimeError("token lapsed")
    market = _market(_chain([560.0, 570.0]))
    stats = merge_held_legs(market, Boom(), [[_position()]], OBS)
    assert stats["merged"] == 0 and stats["error"] is not None


def test_take_profit_fires_on_a_leg_outside_the_chain_window():
    """The regression that matters: same state, same day, one with the merge and
    one without. Credit 8.00, 60% TP -> trigger at ask <= 3.20; the leg is
    offered at 0.44. Unmerged, nothing happens and equity still carries 11.30."""
    cfg = WheelConfig(ticker="TMO", put_delta=0.30, call_delta=0.50, target_dte=11,
                      take_profit_pct=0.60, starting_capital=100_000.0,
                      call_min_strike="basis")
    client = _FakeClient({"TMO   260807P00512500": _quote(0.40, 0.44, mark=0.42)})

    without = PortfolioState(cash=100_000.0, positions=[_position()])
    r0 = step_one_day(without, _market(_chain([560.0, 570.0])), OBS, cfg,
                      selector="chop", n_slots=1)
    assert [t.action for t in r0.trades] == []
    assert without.positions[0]["short"]["last_mid"] == 11.30      # stale, carried

    with_merge = PortfolioState(cash=100_000.0, positions=[_position()])
    market = _market(_chain([560.0, 570.0]))
    merge_held_legs(market, client, [with_merge.positions], OBS)
    r1 = step_one_day(with_merge, market, OBS, cfg, selector="chop", n_slots=1)

    assert [t.action for t in r1.trades] == ["CLOSE_PUT"]
    assert r1.trades[0].price_per_contract == 0.44                 # bought at the ask
    assert with_merge.positions == []                              # campaign closed
    assert r1.equity > r0.equity


def _regime_row():
    return pd.Series({"trend": "chop", "vol": "normal", "vol_pctile": 0.9,
                      "ma50_vs_200": 0.0, "fast_spread": 0.0})


class _EntryMarket(LiveMarket):
    """LiveMarket whose regime always says good-to-rent, so the routing loop
    actually reaches select_contract."""
    def regime_row(self, ticker, day):
        return _regime_row()


def test_a_leg_one_account_holds_is_not_tradeable_by_another_account():
    """The defect this splice introduced, and the reason `held_only` exists.

    run_daily builds ONE LiveMarket and steps all 25 accounts through it, and
    merge_held_legs splices the union of every account's held legs into it
    BEFORE any account steps. The account that owns a leg is protected by the
    `if tk in held_tickers: continue` guard in the routing loop -- the other 24
    are not. Audit 2026-07-31 reproduced this live: account B sold a DOW 99.5P
    that was in its candidate set only because account A held it."""
    chain = _chain([99.0], obs=OBS, expiry=EXP)
    chain["delta"] = -0.24                       # a poor match for a 0.30 target
    closes = pd.Series([103.0] * 300, index=pd.bdate_range(end=OBS, periods=300))
    market = _EntryMarket(["DOW"], {"DOW"}, OBS,
                          closes_fn=lambda tk: closes,
                          chain_fn=lambda tk: chain)

    # account A holds the 99.5P -- outside the pulled window, delta 0.30
    a_positions = [_position(ticker="DOW", strike=99.5, credit=1.30)]
    client = _FakeClient({"DOW   260807P00099500":
                          _quote(1.30, 1.50, mark=1.40, delta=-0.30, und=103.0)})
    stats = merge_held_legs(market, client, [a_positions], OBS)
    assert stats["merged"] == 1

    # account B holds nothing and steps through the SAME market
    cfg = WheelConfig(ticker="DOW", put_delta=0.30, call_delta=0.50,
                      target_dte=(EXP - OBS).days, take_profit_pct=0.60,
                      starting_capital=100_000.0, call_min_strike="basis")
    b = PortfolioState(cash=100_000.0, positions=[])
    r = step_one_day(b, market, OBS, cfg, selector="chop", n_slots=1)

    assert [t.action for t in r.trades] == ["SELL_PUT"]
    assert r.trades[0].contract.strike == 99.0, \
        "account B traded a strike that exists only because account A holds it"


def test_the_mark_only_row_is_still_markable():
    """Unselectable must not mean invisible -- marking it is the whole point."""
    market = _market(_chain([560.0, 570.0]))
    client = _FakeClient({"TMO   260807P00512500": _quote(0.40, 0.44, mark=0.42)})
    merge_held_legs(market, client, [[_position()]], OBS)
    mk = option_mark(market.chain("TMO", OBS), OBS, Contract("TMO", EXP, 512.5, "P"))
    assert mk is not None and mk.ask == 0.44


def test_an_empty_chain_is_not_given_a_synthetic_one_row_chain():
    """`chain_from_json` returns an EMPTY DataFrame, not None, when every
    contract is filtered out (routine for a thin name: bid<=0, ask<=0, or a
    missing delta). Empty is not None, so the "never hand a ticker a chain
    holding only the leg we already hold" guard missed it -- and select_contract
    would then pick that single row by default, selling a strike the bot never
    surveyed, at whatever credit the quote happened to carry."""
    empty = _chain([])
    assert empty is not None and len(empty) == 0
    market = _market(empty)
    client = _FakeClient({"TMO   260807P00512500": _quote(0.0, 0.02, mark=0.01)})
    stats = merge_held_legs(market, client, [[_position()]], OBS)
    assert stats["merged"] == 0
    assert len(market.chain("TMO", OBS)) == 0


def test_a_spliced_row_with_no_delta_keeps_the_column_numeric():
    """Schwab can omit delta (live/data.py documents its "NaN" string), and
    _num turns that into None. Concatenating a None into the chain flips the
    whole delta column to object dtype, and select_contract's `e["delta"].abs()`
    then raises TypeError -- taking down every account routing that ticker for
    the day, silently, via run_daily's per-account except."""
    import numpy as np
    market = _market(_chain([560.0, 570.0]))
    client = _FakeClient({"TMO   260807P00512500":
                          {"quote": {"bidPrice": 0.40, "askPrice": 0.44,
                                     "underlyingPrice": 576.77},
                           "reference": {"daysToExpiration": 7}}})   # no delta
    assert merge_held_legs(market, client, [[_position()]], OBS)["merged"] == 1
    chain = market.chain("TMO", OBS)
    assert chain["delta"].dtype.kind == "f", "delta column went non-numeric"
    assert np.isnan(chain.loc[chain["strike"] == 512.5, "delta"]).all()
    # and the row is still markable
    assert option_mark(chain, OBS, Contract("TMO", EXP, 512.5, "P")).ask == 0.44


def test_a_leg_whose_ticker_chain_failed_is_reported_not_counted_as_fine():
    """`merged: 0` is also what a perfectly healthy run reports (leg already
    inside the window), so the two were indistinguishable. A leg with no chain
    to splice into is NOT marked -- its take-profit is suspended for the day --
    and that is the whole failure this module exists to prevent, reached through
    a second door. It must be counted separately and said out loud."""
    def boom(tk):
        raise RuntimeError("chain pull 502")
    closes = pd.Series([576.77] * 300, index=pd.bdate_range(end=OBS, periods=300))
    market = LiveMarket(["TMO"], {"TMO"}, OBS, closes_fn=lambda tk: closes,
                        chain_fn=boom)
    assert market.chain("TMO", OBS) is None

    client = _FakeClient({"TMO   260807P00512500": _quote(0.40, 0.44, mark=0.42)})
    stats = merge_held_legs(market, client, [[_position()]], OBS)
    assert stats["merged"] == 0
    assert stats["no_chain"] == ["TMO   260807P00512500"]


def test_a_healthy_already_in_window_leg_is_not_reported_as_a_failure():
    market = _market(_chain([512.5, 570.0]))
    client = _FakeClient({"TMO   260807P00512500": _quote(0.40, 0.44, mark=0.42)})
    stats = merge_held_legs(market, client, [[_position()]], OBS)
    assert stats["merged"] == 0
    assert stats["no_chain"] == [] and stats["unquoted"] == []
