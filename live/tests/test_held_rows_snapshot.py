"""A21 (owner-ruled 2026-08-02): held-leg quotes come from the RTH snapshot,
never a 17:00 post-close pull.

D1: marks AND the EOD-backstop TP price off the RTH book (the 17:00 pull fed
the EOD TP branch -- RIG receipt: RTH ask $0.01 fills what the post-close
$0.06 refused). D2: a snapshot day whose held rows are absent steps with
carried marks, EOD TP suspended, ONE alert. D3: anchor break blessed.
A21b rides inside: held rows live under a separate top-level `held_rows` key
(never inside the chains dict -- the store's DataFrame round-trip drops
unknown columns, executed proof in the analyst report), and `held_only=True`
is FORCED at load so the flag cannot die on any round-trip.
"""
import json

import pandas as pd

OBS = pd.Timestamp("2026-07-17")


def _held_row(**over):
    """One serialized held-leg row carrying every engine column, as
    rows_from_quotes builds them (A19 + C1 capture included)."""
    r = {"date": OBS, "expiry": pd.Timestamp("2026-07-31"), "strike": 35.0,
         "right": "P", "dte": 14, "delta": -0.12, "bid": 0.0, "ask": 0.05,
         "mid": 0.025, "underlying": 39.2, "open_interest": 150.0,
         "volume": 12.0, "bid_size": 10.0, "ask_size": 30.0,
         "quote_time": 1789000000000.0, "trade_time": 1788990000000.0,
         "held_only": True}
    r.update(over)
    return r


def test_store_roundtrips_held_rows_and_forces_held_only(tmp_path):
    """A21b/F3: the flag must survive save->load BECAUSE load re-imposes it --
    even a tampered held_only=False in the file comes back True. Timestamps
    come back as Timestamps; stats ride along for the 17:00 stats prints and
    the A10b unquoted escalation."""
    from live.chain_store import save_chain_snapshot, load_held_rows
    p = str(tmp_path / "snap.json")
    save_chain_snapshot(
        OBS, {}, pulled_at="t", path=p,
        held_rows={"GDX": [_held_row(held_only=False)]},   # tampered on purpose
        held_stats={"requested": 1, "answered": 1, "unquoted": []})
    loaded = load_held_rows(OBS, path=p)
    assert loaded is not None
    row = loaded["rows_by_ticker"]["GDX"][0]
    assert row["held_only"] is True, \
        "A21b: held_only must be FORCED at load, not trusted from the file"
    assert row["expiry"] == pd.Timestamp("2026-07-31")
    assert row["date"] == OBS
    assert row["ask"] == 0.05 and row["bid"] == 0.0
    assert (loaded["requested"], loaded["answered"], loaded["unquoted"]) \
        == (1, 1, [])


def test_load_held_rows_absent_vs_empty(tmp_path):
    """None = no held rows in the file (pre-A21 snapshot, or the held pull
    failed all window) -> the D2 path. {} rows with stats = the pull ran fine
    and there were zero held legs -> a quiet healthy merge. The two must
    never collapse into each other."""
    from live.chain_store import save_chain_snapshot, load_held_rows
    p1 = str(tmp_path / "pre_a21.json")
    save_chain_snapshot(OBS, {}, pulled_at="t", path=p1)     # no held_rows key
    assert load_held_rows(OBS, path=p1) is None
    p2 = str(tmp_path / "zero_legs.json")
    save_chain_snapshot(OBS, {}, pulled_at="t", path=p2,
                        held_rows={}, held_stats={"requested": 0,
                                                  "answered": 0,
                                                  "unquoted": []})
    loaded = load_held_rows(OBS, path=p2)
    assert loaded is not None and loaded["rows_by_ticker"] == {}
    # wrong obs date must refuse, same staleness contract as the chains
    assert load_held_rows(OBS + pd.Timedelta(days=1), path=p2) is None
    # garbage rows -> None, never a crash and never a served frame (F1 class)
    p3 = str(tmp_path / "garbage.json")
    payload = json.load(open(p2))
    payload["held_rows"] = {"GDX": [{"strike": "not-a-row"}]}
    json.dump(payload, open(p3, "w"))
    assert load_held_rows(OBS, path=p3) is None


def test_merge_pulled_equals_legacy_merge(monkeypatch):
    """Seam equality: merge_held_legs must be exactly pull_held_quotes |>
    merge_pulled -- same stats dict, same spliced rows -- so the smoke path
    (live pull) and the snapshot path (stored pull) cannot drift."""
    from live.held_legs import (merge_held_legs, pull_held_quotes,
                                merge_pulled)

    quotes = {"GDX   260731P00035000": {
        "quote": {"bidPrice": 0.0, "askPrice": 0.05, "mark": 0.02,
                  "underlyingPrice": 39.2, "quoteTime": 1789000000000},
        "reference": {"daysToExpiration": 14}}}

    class _Client:
        def get_quotes(self, symbols):
            return quotes

    def _positions():
        # one quotable leg + one the endpoint does not answer for -> the
        # unquoted list must survive the seam identically (its consumer is
        # the A10b dead-symbol escalation)
        return [[{"ticker": "GDX", "short": {"contract": {
            "root": "GDX", "expiry": "2026-07-31", "strike": 35.0,
            "right": "P"}}},
            {"ticker": "SLV", "short": {"contract": {
                "root": "SLV", "expiry": "2026-07-31", "strike": 20.0,
                "right": "P"}}}]]

    class _Market:
        def __init__(self):
            self.added = {}
        def add_chain_rows(self, tk, rows):
            self.added.setdefault(tk, []).extend(rows)
            return len(rows)
        def chain(self, tk, obs):
            return object()

    m1, m2 = _Market(), _Market()
    legacy = merge_held_legs(m1, _Client(), _positions(), OBS)
    pulled = pull_held_quotes(_Client(), _positions(), OBS)
    seam = merge_pulled(m2, pulled, OBS)
    assert legacy == seam
    assert m1.added == m2.added
    assert seam["merged"] == 1 and seam["requested"] == 2
    assert len(seam["unquoted"]) == 1 and "SLV" in seam["unquoted"][0], \
        "the unquoted list must ride the seam (A10b escalation reads it)"


def test_daily_run_uses_stored_held_rows_never_get_quotes():
    """THE A21 defect, pinned at the wiring: main() routes held-leg merging
    through resolve_held_merge, and inside THAT helper the live get_quotes
    path (merge_held_legs) is reachable only on smoke. The 17:00 clock
    guarantees any live quote pull there is the post-close ghost book."""
    import inspect
    import re
    import live.run_daily as rd
    src = inspect.getsource(rd.main)
    assert re.search(r"resolve_held_merge\(market, client, position_lists, "
                     r"obs,[\s\S]{0,80}args\.smoke, snap, _alert\)", src), \
        "A21: main() no longer routes held merging through resolve_held_merge"
    assert re.search(r"if not held_d2 and held_marks_failed\(merged\):", src), \
        ("A21-D2: a carried-marks day must not trip held_marks_failed -- no "
         "17:00 retry can rebuild an immutable RTH snapshot (A16-F2 class)")
    hsrc = inspect.getsource(rd.resolve_held_merge)
    live_calls = [m.start() for m in re.finditer(r"merge_held_legs\(", hsrc)]
    smoke_guarded = [m.start() for m in
                     re.finditer(r"if smoke:[\s\S]{0,200}?merge_held_legs\(",
                                 hsrc)]
    assert live_calls and len(live_calls) == len(smoke_guarded), \
        "A21: a live held-leg quote pull is reachable outside --smoke"


def test_resolve_held_merge_branches_behaviorally(tmp_path, monkeypatch):
    """MH2 lesson (a lint pin cannot see dead code): every branch executed.
    A snapshot WITH held rows must actually merge them -- no network, no
    alert; a snapshot without them must take the D2 path; no snapshot at all
    must stay inert; smoke must live-pull."""
    import live.chain_store as cs
    from live.chain_store import save_chain_snapshot
    from live.run_daily import resolve_held_merge

    class _Market:
        def __init__(self):
            self.added = {}
        def add_chain_rows(self, tk, rows):
            self.added.setdefault(tk, []).extend(rows)
            return len(rows)
        def chain(self, tk, obs):
            return object()

    class _NeverClient:
        def get_quotes(self, syms):
            raise AssertionError(
                "A21: 17:00 path touched the live quote endpoint")

    positions = [[{"ticker": "GDX", "short": {"contract": {
        "root": "GDX", "expiry": "2026-07-31", "strike": 35.0,
        "right": "P"}}}]]
    alerts = []
    p = str(tmp_path / "snap.json")
    monkeypatch.setattr(cs, "snapshot_path", lambda obs: p)

    # branch 1: snapshot with held rows -> merged, no alert, no network
    save_chain_snapshot(OBS, {}, pulled_at="t", path=p,
                        held_rows={"GDX": [_held_row()]},
                        held_stats={"requested": 1, "answered": 1,
                                    "unquoted": []})
    m = _Market()
    merged, d2 = resolve_held_merge(m, _NeverClient(), positions, OBS,
                                    False, {}, lambda *a: alerts.append(a))
    assert d2 is False and merged["merged"] == 1 and alerts == []
    assert m.added["GDX"][0]["held_only"] is True

    # branch 2: snapshot without held rows -> D2 (one alert, carried marks)
    save_chain_snapshot(OBS, {}, pulled_at="t", path=p)
    m2 = _Market()
    merged, d2 = resolve_held_merge(m2, _NeverClient(), positions, OBS,
                                    False, {}, lambda *a: alerts.append(a))
    assert d2 is True and merged["error"] and len(alerts) == 1
    assert m2.added == {}

    # branch 3: no snapshot at all -> inert zeros, no alert
    merged, d2 = resolve_held_merge(_Market(), _NeverClient(), positions,
                                    OBS, False, None,
                                    lambda *a: alerts.append(a))
    assert d2 is False and merged["requested"] == 0 and len(alerts) == 1

    # branch 4: smoke -> live pull (client IS touched)
    class _CountingClient:
        calls = 0
        def get_quotes(self, syms):
            _CountingClient.calls += 1
            return {}
    resolve_held_merge(_Market(), _CountingClient(), positions, OBS,
                       True, {}, lambda *a: alerts.append(a))
    assert _CountingClient.calls == 1, "smoke must keep the live probe pull"


def test_runner_failed_held_pull_saves_chains_without_held_rows(
        monkeypatch, tmp_path):
    """Behavioral pin for the runner's failure branch (a source lint cannot
    kill the drop-the-error-branch mutant): a held pull that raises, and one
    that is answered-for-nothing, must BOTH save the chain snapshot WITHOUT
    held kwargs and exit 1 so in-window ticks retry."""
    import datetime as dt
    import sys
    import types
    from zoneinfo import ZoneInfo

    import live.run_chain_snapshot as rcs
    import live.run_daily as rd
    import live.chain_store as cs
    import live.config as lc
    import live.accounts as la
    import live.state as ls

    class _Mkt:
        skipped_closes, chain_attempts, chains_ok = [], 5, 5
        skipped_chains, truncated_closes, _chains = [], [], {}

    class _State:
        positions = [{"ticker": "GDX", "short": {"contract": {
            "root": "GDX", "expiry": "2026-07-31", "strike": 35.0,
            "right": "P"}}}]

    saves = []
    # fixed WEEKDAY inside the 15:20-15:50 window (a now()-derived date lands
    # on weekends when the suite runs on one)
    start = dt.datetime(2026, 7, 17, 15, 30, tzinfo=ZoneInfo("America/New_York"))

    class _DT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return start

    def _wire(client_obj):
        monkeypatch.setattr(rcs.dt, "datetime", _DT)
        fake = types.ModuleType("schwab_client")
        fake.get_client = lambda: client_obj
        monkeypatch.setitem(sys.modules, "schwab_client", fake)
        monkeypatch.setattr(rd, "_live_market", lambda *a, **k: _Mkt())
        monkeypatch.setattr(la, "all_accounts", lambda: [(100_000, 1)])
        monkeypatch.setattr(la, "account_paths",
                            lambda c, n: {"state": str(tmp_path / "s.json")})
        monkeypatch.setattr(ls, "load_state", lambda p: _State())
        monkeypatch.setattr(lc, "load_run_config",
                            lambda *a, **k: {"zombie_threshold": 0.5})
        monkeypatch.setattr(
            cs, "save_chain_snapshot",
            lambda obs, chains, pulled_at, **kw: saves.append(kw)
            or str(tmp_path / "snap.json"))
        monkeypatch.setattr(sys, "argv", ["run_chain_snapshot.py"])

    class _Raises:
        def get_quotes(self, syms):
            raise OSError("dead endpoint")

    class _AnswersNothing:
        def get_quotes(self, syms):
            return {}      # valid dict, zero legs answered -> B4 wholesale

    for client in (_Raises(), _AnswersNothing()):
        saves.clear()
        _wire(client)
        assert rcs.main() == 1, \
            "A21: a failed held pull must exit 1 so in-window ticks retry"
        assert len(saves) == 1 and "held_rows" not in saves[0], \
            "A21: a failed held pull must save chains WITHOUT held rows"


def test_missing_held_rows_day_is_carried_marks_one_alert():
    """A21-D2 (owner): held rows absent -> the day still steps; every held
    leg outside the chain window keeps its carried mark (mark-None suspends
    its TP mechanically); ONE alert names the count; zero held legs -> quiet
    (no alert, no error)."""
    from live.run_daily import missing_held_rows_day
    alerts = []
    positions = [[{"ticker": "GDX", "short": {"contract": {
        "root": "GDX", "expiry": "2026-07-31", "strike": 35.0,
        "right": "P"}}}]]
    stats = missing_held_rows_day(positions, OBS, lambda *a: alerts.append(a))
    assert stats["requested"] == 1 and stats["merged"] == 0
    assert stats["error"], "D2: the day must disclose itself as degraded"
    assert len(alerts) == 1 and "1" in alerts[0][1]
    # zero held legs: a missing held_rows key is then perfectly healthy
    alerts2 = []
    stats2 = missing_held_rows_day([[]], OBS, lambda *a: alerts2.append(a))
    assert alerts2 == [] and stats2["error"] is None
    assert stats2["requested"] == 0


def test_snapshot_runner_saves_held_rows_and_fails_loud_on_pull_error():
    """A21 runner half: run_chain_snapshot.main must call pull_held_quotes and
    pass held_rows/held_stats into save_chain_snapshot; a failed held pull
    saves the snapshot WITHOUT held rows and exits nonzero so remaining
    in-window ticks retry (analyst spec)."""
    import inspect
    import re
    import live.run_chain_snapshot as rcs
    src = inspect.getsource(rcs.main)
    assert re.search(r"pull_held_quotes\(", src), \
        "A21: snapshot runner no longer pulls held-leg quotes in RTH"
    assert re.search(r"held_rows=", src) and re.search(r"held_stats=", src), \
        "A21: snapshot runner no longer persists held rows into the store"


def test_f1_malformed_position_degrades_never_raises():
    """Skeptic F1 (MAJOR): one strike=None position crashed the whole
    snapshot runner -- nothing saved, 25 accounts losing the day with a
    wrong diagnosis. pull_held_quotes' never-raises contract must include
    the contract derivation itself."""
    from live.held_legs import pull_held_quotes

    class _NeverClient:
        def get_quotes(self, syms):
            raise AssertionError("must not reach the endpoint")

    bad = [[{"ticker": "GDX", "short": {"contract": {
        "root": "GDX", "expiry": "2026-07-31", "strike": None,
        "right": "P"}}}]]
    out = pull_held_quotes(_NeverClient(), bad, OBS)
    assert out["error"] and "held_contracts" in out["error"]
    assert out["requested"] == 0 and out["rows_by_ticker"] == {}


def test_f3_corrupt_held_stats_returns_none_not_raise(tmp_path):
    """Skeptic F3: held_stats {"unquoted": null} raised out of the 17:00 run
    on every retry tick. Any malformed byte in the held section -> None ->
    the D2 degrade, never a crash."""
    from live.chain_store import save_chain_snapshot, load_held_rows
    p = str(tmp_path / "snap.json")
    save_chain_snapshot(OBS, {}, pulled_at="t", path=p,
                        held_rows={"GDX": [_held_row()]},
                        held_stats={"requested": 1, "answered": 1,
                                    "unquoted": []})
    payload = json.load(open(p))
    payload["held_stats"] = {"requested": 1, "answered": 1, "unquoted": None}
    json.dump(payload, open(p, "w"))
    assert load_held_rows(OBS, path=p) is None


def test_f6_rows_present_with_empty_stats_still_merge(tmp_path):
    """Skeptic F6: rows are the ground truth -- a file carrying held rows
    with absent/zero stats must still splice them, not silently no-op."""
    from live.chain_store import save_chain_snapshot, load_held_rows
    from live.held_legs import merge_pulled
    p = str(tmp_path / "snap.json")
    save_chain_snapshot(OBS, {}, pulled_at="t", path=p,
                        held_rows={"GDX": [_held_row()]}, held_stats=None)

    class _Market:
        added = {}
        def add_chain_rows(self, tk, rows):
            self.added.setdefault(tk, []).extend(rows)
            return len(rows)
        def chain(self, tk, obs):
            return object()

    loaded = load_held_rows(OBS, path=p)
    assert loaded is not None and loaded["requested"] == 0
    m = _Market()
    stats = merge_pulled(m, loaded, OBS)
    assert stats["merged"] == 1 and m.added["GDX"], \
        "F6: stored rows silently dropped because the stats said zero"


def test_f7_drift_between_snapshot_and_decision_is_disclosed(capsys):
    """Skeptic F7: a leg held at 17:00 but absent from the stored pull
    appeared in NO stat -- TP suspended in total silence. The drift check
    must name it; legs covered by rows or by the unquoted list stay quiet."""
    from live.run_daily import _print_held_drift
    positions = [[
        {"ticker": "GDX", "short": {"contract": {
            "root": "GDX", "expiry": "2026-07-31", "strike": 35.0,
            "right": "P"}}},
        {"ticker": "SLV", "short": {"contract": {
            "root": "SLV", "expiry": "2026-07-31", "strike": 20.0,
            "right": "P"}}},
    ]]
    held = {"rows_by_ticker": {"GDX": [_held_row()]},
            "requested": 1, "answered": 1, "unquoted": []}
    missing = _print_held_drift(positions, held)
    out = capsys.readouterr().out
    assert missing and "SLV" in missing[0]
    assert "HELD-LEG DRIFT" in out and "SLV" in out
    # covered legs -> silent
    held["unquoted"] = missing
    assert _print_held_drift(positions, held) == []
    assert "DRIFT" not in capsys.readouterr().out


def test_runner_good_pull_stores_the_rows_and_failed_tick_keeps_them(
        monkeypatch, tmp_path):
    """Skeptic F5: the success path was pinned only by text regexes -- a
    mutant saving held_rows={} passed every pin while silently killing every
    TP. Executed now: a good pull's row must actually reach the store.
    Skeptic F2: a later tick whose held pull FAILS must carry the earlier
    tick's good rows forward, not clobber them."""
    import datetime as dt
    import sys
    import types
    from zoneinfo import ZoneInfo

    import live.run_chain_snapshot as rcs
    import live.run_daily as rd
    import live.chain_store as cs
    import live.config as lc
    import live.accounts as la
    import live.state as ls

    class _Mkt:
        skipped_closes, chain_attempts, chains_ok = [], 5, 5
        skipped_chains, truncated_closes, _chains = [], [], {}

    class _State:
        positions = [{"ticker": "GDX", "short": {"contract": {
            "root": "GDX", "expiry": "2026-07-31", "strike": 35.0,
            "right": "P"}}}]

    start = dt.datetime(2026, 7, 17, 15, 30,
                        tzinfo=ZoneInfo("America/New_York"))

    class _DT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return start

    snap_path = str(tmp_path / "snap.json")

    def _wire(client_obj):
        monkeypatch.setattr(rcs.dt, "datetime", _DT)
        fake = types.ModuleType("schwab_client")
        fake.get_client = lambda: client_obj
        monkeypatch.setitem(sys.modules, "schwab_client", fake)
        monkeypatch.setattr(rd, "_live_market", lambda *a, **k: _Mkt())
        monkeypatch.setattr(la, "all_accounts", lambda: [(100_000, 1)])
        monkeypatch.setattr(la, "account_paths",
                            lambda c, n: {"state": str(tmp_path / "s.json")})
        monkeypatch.setattr(ls, "load_state", lambda p: _State())
        monkeypatch.setattr(lc, "load_run_config",
                            lambda *a, **k: {"zombie_threshold": 0.5})
        monkeypatch.setattr(cs, "snapshot_path", lambda obs: snap_path)
        monkeypatch.setattr(sys, "argv", ["run_chain_snapshot.py"])

    class _GoodClient:
        def get_quotes(self, syms):
            return {syms[0]: {
                "quote": {"bidPrice": 0.0, "askPrice": 0.05, "mark": 0.02,
                          "underlyingPrice": 39.2,
                          "quoteTime": 1789000000000},
                "reference": {"daysToExpiration": 14}}}

    # tick 1: good pull -> the row is IN the store (F5, executed)
    _wire(_GoodClient())
    assert rcs.main() == 0
    obs = pd.Timestamp("2026-07-17")
    loaded = cs.load_held_rows(obs, path=snap_path)
    assert loaded is not None and loaded["rows_by_ticker"]["GDX"], \
        "F5: a good pull's rows never reached the store"
    assert loaded["rows_by_ticker"]["GDX"][0]["ask"] == 0.05
    assert loaded["requested"] == 1 and loaded["answered"] == 1

    # tick 2: held pull dies -> exit 1, but tick 1's rows survive (F2)
    class _DeadClient:
        def get_quotes(self, syms):
            raise OSError("endpoint died")
    _wire(_DeadClient())
    assert rcs.main() == 1
    kept = cs.load_held_rows(obs, path=snap_path)
    assert kept is not None and kept["rows_by_ticker"]["GDX"], \
        "F2: a later failed tick clobbered the earlier tick's good held rows"
