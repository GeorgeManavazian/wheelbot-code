"""A9, live half: the deferral must survive a state reload, because live can
re-step the SAME session in the save-succeeded/snapshot-failed retry window
(the A5 lesson -- an in-memory flag evaporates on reload and the retry would
sell the call on assignment day after all). And per A8b, a future real-money
reconciler mutates positions from a different process -- only a persisted
`assigned_d` lets the 17:00 step see "assigned earlier today"."""
import pandas as pd

from src.engine_v2.options.chain import Contract
from src.engine_v2.options.portfolio import PortfolioState, step_one_day
from src.engine_v2.options.wheel import WheelConfig
from live.state import save_state, load_state

COLS = ["date", "expiry", "dte", "strike", "right", "bid", "ask", "mid",
        "close", "delta", "iv", "underlying"]
D0 = pd.Timestamp("2024-01-09")
D1 = pd.Timestamp("2024-01-10")


def _chain(rows):
    ch = pd.DataFrame(rows, columns=COLS)
    ch["date"] = pd.to_datetime(ch["date"])
    ch["expiry"] = pd.to_datetime(ch["expiry"])
    return ch


class _Market:
    universe = []

    def __init__(self):
        self._ch = {
            D0: _chain([[D0, D0 + pd.Timedelta(days=7), 7, 32.0, "C",
                         1.00, 1.10, 1.05, 1.05, 0.30, 0.2, 28.0]]),
            D1: _chain([[D1, D1 + pd.Timedelta(days=6), 6, 32.0, "C",
                         0.90, 1.00, 0.95, 0.95, 0.30, 0.2, 28.5]]),
        }

    def chain(self, tk, d):
        return self._ch.get(d)

    def spot(self, tk, d, fb):
        return 28.0

    def settle_price(self, tk, expiry):
        return 28.0

    def regime_row(self, tk, d):
        return None

    def eligible(self, tk, d):
        return True


def _cfg():
    return WheelConfig(ticker="GDX", put_delta=0.20, call_delta=0.30,
                       target_dte=7, take_profit_pct=None,
                       starting_capital=100_000.0,
                       commission_per_contract=0.0, call_min_strike="basis")


def test_deferral_survives_reload_and_same_day_restep(tmp_path):
    put = Contract("GDX", D0, 30.0, "P")
    pos = {"ticker": "GDX", "shares": 0, "phase": "PUT", "basis": None,
           "premium": 120.0, "campaign": 1, "last_spot": 28.0,
           "short": {"contract": put, "contracts": 1, "credit": 1.2,
                     "last_mid": 1.2}}
    state = PortfolioState(cash=100_120.0, positions=[pos], campaign=1)
    mkt = _Market()
    p = str(tmp_path / "state.json")

    r0 = step_one_day(state, mkt, D0, _cfg(), selector="plain", n_slots=1)
    assert [t.action for t in r0.trades] == ["ASSIGNED"]

    # the retry window: state saved, snapshot append failed, 5-min retry
    # reloads from disk and steps the SAME day again
    save_state(state, p)
    reloaded = load_state(p)
    r_retry = step_one_day(reloaded, mkt, D0, _cfg(), selector="plain", n_slots=1)
    assert [t.action for t in r_retry.trades] == [], \
        "A9: the deferral evaporated on reload -- the retry window sold the call"

    # next session sells normally, from the reloaded state
    r1 = step_one_day(reloaded, mkt, D1, _cfg(), selector="plain", n_slots=1)
    assert [t.action for t in r1.trades] == ["SELL_CALL"]


def test_positions_without_assigned_d_stay_key_identical(tmp_path):
    """Legacy state files must stay byte-identical (last_ask precedent)."""
    pos = {"ticker": "XYZ", "shares": 0, "phase": "PUT", "basis": None,
           "premium": 0.0, "campaign": 1, "last_spot": 104.0, "short": None}
    p = str(tmp_path / "state.json")
    save_state(PortfolioState(cash=1.0, positions=[pos]), p)
    got = load_state(p).positions[0]
    assert "assigned_d" not in got, \
        "A9: absent assigned_d must stay absent (legacy files byte-identical)"
