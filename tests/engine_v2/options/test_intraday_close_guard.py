"""A5: the same-day anti-churn guard (`closed_today`) is rebuilt empty on
every step_one_day call, and the intraday manager persists nothing about its
closes -- so the 17:00 run happily re-sells the exact contract the intraday
manager bought back at 10:00. A6/A16 exist to INCREASE intraday closes, which
makes this guard's blindness worse every day. Fix: intraday closes are
recorded on the state (date-stamped) and seed `closed_today` for same-day
steps; a stale record (yesterday's) seeds nothing."""
import pandas as pd

from src.engine_v2.options.chain import Contract, Mark
from src.engine_v2.options.portfolio import PortfolioState, step_one_day
from src.engine_v2.options.wheel import WheelConfig

D = pd.Timestamp("2026-07-21")
COLS = ["date", "expiry", "dte", "strike", "right", "bid", "ask", "mid",
        "close", "delta", "iv", "underlying"]


def _chain():
    # one entry-grade put: the same contract the intraday manager just closed
    ch = pd.DataFrame([[D, D + pd.Timedelta(days=11), 11, 40.0, "P", 1.00,
                        1.10, 1.05, 1.05, -0.30, 0.2, 41.0]], columns=COLS)
    ch["date"] = pd.to_datetime(ch["date"])
    ch["expiry"] = pd.to_datetime(ch["expiry"])
    return ch


class M:
    universe = ["GDX"]

    def chain(self, tk, d):
        return _chain()

    def spot(self, tk, d, fb):
        return 41.0

    def settle_price(self, tk, expiry):
        return 41.0

    def regime_row(self, tk, day):
        return None

    def eligible(self, tk, day):
        return True


def _cfg():
    return WheelConfig(ticker="GDX", starting_capital=100_000.0, put_delta=0.30,
                       call_delta=0.50, target_dte=11, take_profit_pct=0.60,
                       call_min_strike="basis")


def _state_with_short(credit=2.50, n=2):
    c = Contract("GDX", D + pd.Timedelta(days=11), 40.0, "P")
    return PortfolioState(cash=100_000.0, positions=[{
        "ticker": "GDX", "shares": 0, "phase": "PUT", "basis": None,
        "premium": credit * 100 * n, "campaign": 1, "last_spot": 41.0,
        "short": {"contract": c, "contracts": n, "credit": credit,
                  "last_mid": credit}}])


def test_intraday_close_blocks_same_day_reentry():
    """10:00: intraday manager closes the GDX 40P at TP. 17:00: the EOD step
    must NOT re-sell that same contract the same day."""
    from live.intraday import manage_intraday
    st = _state_with_short()
    trades = manage_intraday(st, {"GDX": Mark(0.90, 0.95, 0.92)}, _cfg(),
                             pd.Timestamp("2026-07-21 10:00"))
    assert [t.action for t in trades] == ["CLOSE_PUT"]
    assert st.positions == []          # emptied slot compacted
    r = step_one_day(st, M(), D, _cfg(), selector="plain", n_slots=1)
    assert [t.action for t in r.trades] == [], \
        "A5: EOD step re-sold the contract the intraday manager closed today"


def test_yesterdays_intraday_close_does_not_block_today():
    from live.intraday import manage_intraday
    st = _state_with_short()
    manage_intraday(st, {"GDX": Mark(0.90, 0.95, 0.92)}, _cfg(),
                    pd.Timestamp("2026-07-20 10:00"))   # closed YESTERDAY
    r = step_one_day(st, M(), D, _cfg(), selector="plain", n_slots=1)
    assert [t.action for t in r.trades] == ["SELL_PUT"]


def test_eod_close_still_blocks_reentry_as_before():
    # the pre-existing same-step guard is unchanged: an EOD TP close and a
    # re-entry candidate in the same step never churn. credit 3.00 -> thresh
    # 1.20 >= ask 1.10 -> TP fires; the entry loop then sees the same
    # contract and must skip it.
    st = _state_with_short(credit=3.00)
    r = step_one_day(st, M(), D, _cfg(), selector="plain", n_slots=1)
    assert [t.action for t in r.trades] == ["CLOSE_PUT"]


def test_state_roundtrip_preserves_the_guard(tmp_path):
    """The 17:00 run RELOADS state from disk (run_daily loads every account
    fresh) -- an intraday close that does not survive the round trip guards
    nothing."""
    from live.intraday import manage_intraday
    from live.state import save_state, load_state
    st = _state_with_short()
    manage_intraday(st, {"GDX": Mark(0.90, 0.95, 0.92)}, _cfg(),
                    pd.Timestamp("2026-07-21 10:00"))
    p = str(tmp_path / "state.json")
    save_state(st, p)
    back = load_state(p)
    r = step_one_day(back, M(), D, _cfg(), selector="plain", n_slots=1)
    assert [t.action for t in r.trades] == [], \
        "A5: the guard evaporated in the state round trip"


def test_eod_close_survives_crash_retry_of_the_same_day(tmp_path):
    """Skeptic F1: EOD closes left no persistent note, so in the narrow
    save-succeeded/snapshot-append-failed window a same-day re-step re-sold
    the exact contract run 1 closed. The step now records its own closes in
    the same date-stamped list."""
    from live.state import save_state, load_state
    st = _state_with_short(credit=3.00)          # thresh 1.20 >= ask 1.10
    r1 = step_one_day(st, M(), D, _cfg(), selector="plain", n_slots=1)
    assert [t.action for t in r1.trades] == ["CLOSE_PUT"]
    p = str(tmp_path / "state.json")
    save_state(st, p)                            # crash after save, before snapshot
    back = load_state(p)
    r2 = step_one_day(back, M(), D, _cfg(), selector="plain", n_slots=1)
    assert [t.action for t in r2.trades] == [], \
        "A5/F1: the retry re-sold the contract the first run closed"
