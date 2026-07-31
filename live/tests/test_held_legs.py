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


def _quote(bid, ask, mark=None, delta=-0.05, dte=7, und=576.77):
    q = {"quote": {"bidPrice": bid, "askPrice": ask, "delta": delta,
                   "underlyingPrice": und},
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


def test_row_built_for_a_leg_outside_the_chain_window():
    contracts = held_contracts([[_position()]])
    rows = rows_from_quotes({"TMO   260807P00512500": _quote(0.40, 0.44, mark=0.42)},
                            contracts, OBS)
    assert list(rows) == ["TMO"]
    r = rows["TMO"][0]
    assert set(r) == set(_CHAIN_COLS)
    assert r["date"] == OBS and r["expiry"] == EXP
    assert (r["strike"], r["right"]) == (512.5, "P")
    assert (r["bid"], r["ask"], r["mid"]) == (0.40, 0.44, 0.42)
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
