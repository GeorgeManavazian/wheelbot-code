import datetime as dt
import sys
import types
from zoneinfo import ZoneInfo

import pandas as pd

from live.run_chain_snapshot import snapshot_window_open, save_still_rth

ET = ZoneInfo("America/New_York")


# ---- E8: main() wiring harness -------------------------------------------
# The predicates above are unit-tested; until now the WIRING (zombie exit,
# partial-save-exit-1, the save-recheck call site, --force) was executed only
# by one skeptic run. Everything external is monkeypatched at the module that
# owns it (main() re-imports inside the function, so call-time attribute
# patches land); no network, no real store.

class _FakeMarket:
    def __init__(self, skipped_closes=0, chain_attempts=3, chains_ok=3,
                 skipped_chains=()):
        self.skipped_closes = [None] * skipped_closes
        self.chain_attempts = chain_attempts
        self.chains_ok = chains_ok
        self.skipped_chains = list(skipped_chains)
        self.truncated_closes = []
        self._chains = {"GDX": pd.DataFrame({"strike": [30.0], "right": ["C"]})}

    def add_chain_rows(self, tk, rows):
        return 0


def _clockseq(*stamps):
    """dt.datetime replacement whose now() pops stamps in order (start clock,
    save-recheck clock), then repeats the last one."""
    stamps = list(stamps)

    class _DT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            s = stamps.pop(0) if len(stamps) > 1 else stamps[0]
            return s
    return _DT


def _wire(monkeypatch, tmp_path, market, start=None, done=None, argv=None):
    import live.run_chain_snapshot as rcs
    import live.run_daily as rd
    import live.chain_store as cs
    import live.config as lc
    import live.accounts as la
    import live.state as ls
    start = start or _at(17, 15, 30)
    done = done or start
    monkeypatch.setattr(rcs.dt, "datetime", _clockseq(start, done))
    fake = types.ModuleType("schwab_client")
    fake.get_client = lambda: object()
    monkeypatch.setitem(sys.modules, "schwab_client", fake)
    monkeypatch.setattr(rd, "_live_market", lambda *a, **k: market)
    monkeypatch.setattr(la, "all_accounts", lambda: [])
    monkeypatch.setattr(lc, "load_run_config",
                        lambda *a, **k: {"zombie_threshold": 0.5})
    saved = []
    monkeypatch.setattr(cs, "save_chain_snapshot",
                        lambda obs, chains, pulled_at: saved.append(obs)
                        or str(tmp_path / "snap.parquet"))
    monkeypatch.setattr(sys, "argv", argv or ["run_chain_snapshot.py"])
    return rcs, saved


def test_e8_zombie_wiring_exits_1_and_saves_nothing(monkeypatch, tmp_path):
    """A dead feed (all closes failed) must trip zombie_check -> exit 1,
    NOTHING saved -- the tick retries inside the window."""
    m = _FakeMarket(skipped_closes=550, chain_attempts=10, chains_ok=1)
    rcs, saved = _wire(monkeypatch, tmp_path, m)
    assert rcs.main() == 1
    assert saved == [], "E8: a zombie snapshot was saved"


def test_e8_partial_saves_anyway_but_exits_1(monkeypatch, tmp_path):
    """Skeptic F3 semantics: a sub-threshold chain failure SAVES the partial
    snapshot (17:00 uses the best one written) but exits 1 so remaining
    window ticks retry a cleaner pull."""
    m = _FakeMarket(skipped_chains=[("XOP", "boom")])
    rcs, saved = _wire(monkeypatch, tmp_path, m)
    assert rcs.main() == 1
    assert len(saved) == 1, "E8: the partial snapshot was not saved"


def test_e8_save_recheck_discards_a_slow_pull(monkeypatch, tmp_path):
    """Started in-window, finished past the 16:05 grace: the save-recheck
    CALL SITE must discard (predicate alone was tested; the wiring wasn't)."""
    m = _FakeMarket()
    rcs, saved = _wire(monkeypatch, tmp_path, m,
                       start=_at(17, 15, 45), done=_at(17, 16, 20))
    assert rcs.main() == 1
    assert saved == [], "E8: post-close quotes were blessed as RTH"


def test_e8_clean_run_saves_and_exits_0(monkeypatch, tmp_path):
    m = _FakeMarket()
    rcs, saved = _wire(monkeypatch, tmp_path, m)
    assert rcs.main() == 0
    assert len(saved) == 1


def test_e8_force_bypasses_window_but_still_pulls(monkeypatch, tmp_path):
    """--force at 17:30 ET (both gates shut) must still pull and save --
    the documented manual/backfill path."""
    m = _FakeMarket()
    rcs, saved = _wire(monkeypatch, tmp_path, m,
                       start=_at(17, 17, 30), done=_at(17, 17, 31),
                       argv=["run_chain_snapshot.py", "--force"])
    assert rcs.main() == 0
    assert len(saved) == 1


def test_e8_no_force_outside_window_refuses_before_client(monkeypatch, tmp_path):
    m = _FakeMarket()
    rcs, saved = _wire(monkeypatch, tmp_path, m, start=_at(17, 17, 30))
    fake = types.ModuleType("schwab_client")
    fake.get_client = lambda: (_ for _ in ()).throw(
        AssertionError("refused snapshot must never build a client"))
    monkeypatch.setitem(sys.modules, "schwab_client", fake)
    assert rcs.main() == 1
    assert saved == []


def _at(day, h, m):
    return dt.datetime(2026, 7, day, h, m, tzinfo=ET)


def test_window_open_inside_rth():
    assert snapshot_window_open(_at(17, 15, 20))      # Friday, open edge
    assert snapshot_window_open(_at(17, 15, 45))
    assert snapshot_window_open(_at(17, 15, 50))      # close edge


def test_window_shut_outside():
    assert not snapshot_window_open(_at(17, 15, 19))
    # 15:51: a start here plus the measured ~7-min pull would finish past the
    # 16:00 close (skeptic F4) -- shut.
    assert not snapshot_window_open(_at(17, 15, 51))
    assert not snapshot_window_open(_at(17, 9, 30))


def test_save_recheck_refuses_post_close_finish():
    """Skeptic F4: the start gate alone let a slow pull bless post-close
    quotes as RTH; the save must re-check the clock, with a small grace for a
    pull that read the closing book seconds late."""
    assert save_still_rth(_at(17, 15, 59))
    assert save_still_rth(_at(17, 16, 5))     # grace edge
    assert not save_still_rth(_at(17, 16, 6))
    assert not save_still_rth(_at(17, 17, 0))


def test_seventeen_hundred_is_the_defect_not_the_window():
    """A16 regression pin: 17:00 ET is after the options close; a snapshot
    then is exactly the ghost book this fix removes."""
    assert not snapshot_window_open(_at(17, 17, 0))


def test_weekend_shut():
    assert not snapshot_window_open(_at(18, 15, 45))  # Saturday
    assert not snapshot_window_open(_at(19, 15, 45))  # Sunday


def _frame(rows):
    import pandas as pd
    COLS = ["date", "expiry", "dte", "strike", "right", "bid", "ask", "mid",
            "close", "delta", "iv", "underlying"]
    df = pd.DataFrame(rows, columns=COLS)
    df["date"] = pd.to_datetime(df["date"])
    df["expiry"] = pd.to_datetime(df["expiry"])
    return df


def test_additive_call_rows_strictly_above_existing_window():
    """A4 splice filter: only strikes strictly above the primary chain's
    per-expiry max call strike, only for expiries the primary already has
    call rows for -- purely additive at the top, provable non-regression."""
    import pandas as pd
    from live.run_chain_snapshot import additive_call_rows
    D = pd.Timestamp("2026-07-21")
    E1, E2 = D + pd.Timedelta(days=11), D + pd.Timedelta(days=25)
    primary = _frame([
        [D, E1, 11, 70.0, "C", 1.0, 1.1, 1.05, 1.05, 0.5, 0.2, 70.0],
        [D, E1, 11, 72.0, "C", 0.8, 0.9, 0.85, 0.85, 0.4, 0.2, 70.0],
        [D, E1, 11, 69.0, "P", 1.0, 1.1, 1.05, 1.05, -0.4, 0.2, 70.0]])
    wide = _frame([
        [D, E1, 11, 72.0, "C", 0.8, 0.9, 0.85, 0.85, 0.4, 0.2, 70.0],   # dup of top
        [D, E1, 11, 75.0, "C", 0.4, 0.5, 0.45, 0.45, 0.2, 0.2, 70.0],   # above -> keep
        [D, E1, 11, 80.0, "C", 0.1, 0.2, 0.15, 0.15, 0.1, 0.2, 70.0],   # above -> keep
        [D, E2, 25, 75.0, "C", 0.6, 0.7, 0.65, 0.65, 0.2, 0.2, 70.0]])  # NEW expiry -> drop
    rows = additive_call_rows(primary, wide)
    assert [(r["strike"], pd.Timestamp(r["expiry"])) for r in rows] == \
        [(75.0, E1), (80.0, E1)]


def test_additive_call_rows_empty_cases():
    import pandas as pd
    from live.run_chain_snapshot import additive_call_rows
    D = pd.Timestamp("2026-07-21")
    E1 = D + pd.Timedelta(days=11)
    puts_only = _frame([[D, E1, 11, 69.0, "P", 1.0, 1.1, 1.05, 1.05, -0.4,
                         0.2, 70.0]])
    wide = _frame([[D, E1, 11, 80.0, "C", 0.1, 0.2, 0.15, 0.15, 0.1, 0.2, 70.0]])
    assert additive_call_rows(puts_only, wide) == []     # no primary calls
    assert additive_call_rows(wide, None) == []
    assert additive_call_rows(wide, wide.iloc[0:0]) == []


def test_spliced_rows_reach_the_floor_through_selection():
    """End-to-end through the real market seam: spliced OTM rows must be
    TRADEABLE (no held_only flag) so select_contract(min_strike=floor) can
    finally return the floor-reaching strike."""
    import pandas as pd
    from live.run_chain_snapshot import additive_call_rows
    from live.market_live import LiveMarket
    from live.tests.test_run_daily import PH   # GDX price history fixture
    from live.data import closes_from_json
    from src.engine_v2.options.select import select_contract
    D = pd.Timestamp("2026-07-17")
    E1 = D + pd.Timedelta(days=11)
    primary = _frame([
        [D, E1, 11, 70.0, "C", 1.0, 1.1, 1.05, 1.05, 0.5, 0.2, 70.0],
        [D, E1, 11, 72.0, "C", 0.8, 0.9, 0.85, 0.85, 0.4, 0.2, 70.0]])
    wide = _frame([
        [D, E1, 11, 79.0, "C", 0.1, 0.2, 0.15, 0.15, 0.08, 0.2, 70.0],
        [D, E1, 11, 80.0, "C", 0.05, 0.15, 0.1, 0.1, 0.05, 0.2, 70.0]])
    m = LiveMarket(["GDX"], {"GDX"}, D,
                   closes_fn=lambda tk: closes_from_json(PH),
                   chain_fn=lambda tk: primary)
    added = m.add_chain_rows("GDX", additive_call_rows(primary, wide))
    assert added == 2
    c = select_contract(m.chain("GDX", D), D, "C", 0.50, 11, "GDX",
                        min_strike=79.0)
    assert c is not None and c.strike == 79.0


def test_additive_call_rows_rejects_garbage_delta():
    """Skeptic F1: a plausible-but-wrong delta (~0.49) on a far-OTM spliced
    row would WIN |delta-0.50| selection and silently move the sold strike.
    Monotonicity guard: spliced |delta| must sit strictly below the primary's
    minimum call |delta| for that expiry."""
    import pandas as pd
    from live.run_chain_snapshot import additive_call_rows
    D = pd.Timestamp("2026-07-21")
    E1 = D + pd.Timedelta(days=11)
    primary = _frame([
        [D, E1, 11, 70.0, "C", 1.0, 1.1, 1.05, 1.05, 0.50, 0.2, 70.0],
        [D, E1, 11, 72.0, "C", 0.8, 0.9, 0.85, 0.85, 0.40, 0.2, 70.0]])
    wide = _frame([
        [D, E1, 11, 85.0, "C", 0.05, 0.10, 0.07, 0.07, 0.49, 0.2, 70.0],  # liar
        [D, E1, 11, 86.0, "C", 0.05, 0.10, 0.07, 0.07, 0.12, 0.2, 70.0]]) # honest
    rows = additive_call_rows(primary, wide)
    assert [r["strike"] for r in rows] == [86.0], \
        "a far-OTM row claiming near-the-money delta must be dropped"
