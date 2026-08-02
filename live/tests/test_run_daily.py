import json
import pandas as pd
from live.data import closes_from_json, chain_from_json
from live.market_live import LiveMarket
from live.run_daily import paper_step
from live.state import load_state
from src.engine_v2.options.portfolio import PortfolioState
from src.engine_v2.options.wheel import WheelConfig

OBS = pd.Timestamp("2026-07-17")
PH = json.load(open("live/fixtures/price_history_gdx.json"))
OC = json.load(open("live/fixtures/option_chain_gdx_puts.json"))


def test_daily_chains_come_from_snapshot_not_live_pull(monkeypatch):
    """A16: the daily run executes at 17:00 ET, after the options close, where
    the book is 3-4x wider than anything tradeable (median rel-spread 29.8% vs
    7.4% intraday). The run-time market must therefore consume the RTH chain
    snapshot and must NEVER pull an option chain live -- a live pull at run time
    prices entries and selects strikes off a post-close ghost book."""
    import live.data as data
    from live import run_daily
    pulled = []

    def live_chain_pull(client, tk, target_dte, strike_count=12, obs_date=None):
        pulled.append(tk)
        return chain_from_json(OC, OBS)

    monkeypatch.setattr(data, "daily_closes", lambda client, tk: closes_from_json(PH))
    monkeypatch.setattr(data, "chain_frame", live_chain_pull)
    snap_chain = chain_from_json(OC, OBS)
    m = run_daily._live_market(["GDX"], {"GDX"}, OBS, object(), 11,
                               {"GDX": snap_chain})
    assert pulled == [], (
        "A16: run-time market pulled option chains live (post-close book)")
    # and the snapshot is actually what gets served
    assert m.chain("GDX", OBS) is snap_chain


def test_candidate_missing_from_snapshot_is_a_failed_pull(monkeypatch):
    """A candidate absent from the snapshot must land in skipped_chains (an
    honest failed pull the zombie gate can judge), not silently price off
    nothing and not fall back to a live pull."""
    import live.data as data
    from live import run_daily
    pulled = []
    monkeypatch.setattr(data, "daily_closes", lambda client, tk: closes_from_json(PH))
    monkeypatch.setattr(data, "chain_frame",
                        lambda *a, **k: pulled.append(a) or chain_from_json(OC, OBS))
    m = run_daily._live_market(["GDX"], {"GDX"}, OBS, object(), 11, {})
    assert pulled == []
    assert m.chain_attempts == 1 and m.chains_ok == 0
    assert [tk for tk, _ in m.skipped_chains] == ["GDX"]


def test_incomplete_snapshot_skips_day_instead_of_retry_spam(tmp_path, monkeypatch):
    """Skeptic F2: a candidate absent from a PRESENT snapshot used to trip the
    zombie path -- exit 1, retried and alerted every 5 minutes until 23:30,
    diagnosed as a lapsed token -- even though no retry can rebuild a 15:2x
    snapshot. It must instead skip the day once, truthfully labelled, exit 0.
    Also pins A16 end-to-end: main() must never call the live chain endpoint."""
    import datetime as dt
    import sys
    import types
    from zoneinfo import ZoneInfo
    import live.data as data
    import live.market_live as ml
    from live import run_daily
    from live.chain_store import save_chain_snapshot

    monkeypatch.setenv("WHEELBOT_STATE_DIR", str(tmp_path))
    obs = pd.Timestamp(dt.datetime.now(ZoneInfo("America/New_York")).date())
    s = closes_from_json(PH)
    s = pd.concat([s, pd.Series([float(s.iloc[-1])], index=[obs])])

    monkeypatch.setattr(run_daily, "UNIVERSE", ["GDX"])
    monkeypatch.setattr(ml, "is_good_renting_weather", lambda *a, **k: True)
    monkeypatch.setattr(data, "daily_closes", lambda c, tk: s)

    def no_live_chains(*a, **k):
        raise AssertionError("A16: main() pulled an option chain live")
    monkeypatch.setattr(data, "chain_frame", no_live_chains)

    save_chain_snapshot(obs, {}, pulled_at="t")   # present but missing GDX
    alerts, gaps = [], []
    monkeypatch.setattr(run_daily, "send_alert", lambda *a: alerts.append(a))
    # isolate from the real archive's held legs (accounts.py binds the store
    # at import time) and from B4's wholesale-failure gate -- neither is this
    # test's subject
    monkeypatch.setattr(run_daily, "merge_held_legs",
                        lambda *a, **k: {"requested": 0, "merged": 0,
                                         "unquoted": [], "no_chain": [],
                                         "error": None})
    monkeypatch.setattr(run_daily, "append_gap",
                        lambda date, reason, **kw: gaps.append((str(date), reason)))
    fake = types.ModuleType("schwab_client")
    fake.get_client = lambda: object()
    monkeypatch.setitem(sys.modules, "schwab_client", fake)
    # --force: this test exercises the snapshot gate, not the A11 clock gate
    monkeypatch.setattr(sys, "argv", ["run_daily.py", "--force"])

    rc = run_daily.main()
    assert rc == 0, "must exit 0 (marker written, no retry spam)"
    assert gaps == [(str(obs.date()), "chain_snapshot_incomplete")]
    assert len(alerts) == 1


def _fixture_market(chains, closes_fn=None, obs=OBS):
    return LiveMarket(["GDX"], {"GDX"}, obs,
                      closes_fn=closes_fn or (lambda tk: closes_from_json(PH)),
                      chain_fn=lambda tk: chains[tk])


def test_snapshot_missing_outcomes():
    """A16 owner decision: missing snapshot on a trading day -> gap; on a
    holiday -> quiet exit; with the closes feed itself dead -> retry (a dead
    feed must not be misread as a holiday by an empty market)."""
    from live.run_daily import snapshot_missing_outcome
    chain = {"GDX": chain_from_json(OC, OBS)}
    # real trading day (fixture has a bar dated OBS) -> gap
    m = _fixture_market(chain)
    assert snapshot_missing_outcome(m, ["GDX"], OBS, 0.5) == "gap"
    # holiday: closes exist but none dated obs -> holiday
    m = _fixture_market(chain, obs=OBS + pd.Timedelta(days=1))
    assert snapshot_missing_outcome(m, ["GDX"], OBS + pd.Timedelta(days=1), 0.5) == "holiday"
    # closes feed dead -> retry, NOT holiday
    def dead(tk):
        raise RuntimeError("HTTP 401")
    m = _fixture_market(chain, closes_fn=dead)
    assert snapshot_missing_outcome(m, ["GDX"], OBS, 0.5) == "retry"


def test_paper_step_persists_state_and_trades(tmp_path):
    m = LiveMarket(["GDX"], set(), OBS,
                   closes_fn=lambda tk: closes_from_json(PH),
                   chain_fn=lambda tk: chain_from_json(OC, OBS))
    cfg = WheelConfig(ticker="GDX", put_delta=0.30, call_delta=0.50, target_dte=11,
                      take_profit_pct=0.60, starting_capital=100_000.0,
                      call_min_strike="basis")
    state = PortfolioState(cash=100_000.0, positions=[])
    sp = str(tmp_path / "state.json"); tp = str(tmp_path / "trades.jsonl")
    r = paper_step(state, m, cfg, n_slots=1, trades_path=tp, state_path=sp)
    # state.json written and reloadable
    reloaded = load_state(sp)
    assert reloaded is not None and reloaded.prev_d == OBS
    # each trade is one JSON line in trades.jsonl
    with open(tp) as f:
        lines = [json.loads(x) for x in f if x.strip()]
    assert len(lines) == len(r.trades)
    if lines:
        assert {"date", "action", "ticker"} <= set(lines[0])


def test_decision_run_refuses_before_the_close(monkeypatch):
    """A11: the 2026-07-24 catch-up ran at 04:13 ET and booked every entry
    off the prior day's quotes. The decision run must refuse to step before
    16:00 ET (settlement and marks need the official close) unless --force.
    Exit must be NONZERO so the tick writes no done-marker and retries after
    17:00 as designed."""
    import datetime as dt
    import sys
    import types
    from zoneinfo import ZoneInfo
    from live import run_daily

    class _FrozenDT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 7, 24, 4, 13, tzinfo=tz)

    monkeypatch.setattr(run_daily.dt, "datetime", _FrozenDT)
    fake = types.ModuleType("schwab_client")
    fake.get_client = lambda: (_ for _ in ()).throw(
        AssertionError("A11: refused run must never build a client"))
    monkeypatch.setitem(sys.modules, "schwab_client", fake)
    monkeypatch.setattr(sys, "argv", ["run_daily.py"])
    rc = run_daily.main()
    assert rc not in (0, None), \
        "A11: a 04:13 run must refuse with a nonzero exit (no marker, retry later)"


def test_frozen_carries_the_a13_fee_constants():
    """A13: production charges pass-through fees (OCC/ORF/SEC/TAF ~ $0.05 per
    contract-side, provisional pending a real statement) and a $0 Schwab
    assignment fee (VERIFY on first real statement). Dataclass defaults stay
    0.0 so goldens/backtests are byte-identical; FROZEN is where production
    diverges -- pin it so a config refactor can't silently drop the fee."""
    from live.run_daily import FROZEN
    assert FROZEN["fees_per_contract"] == 0.05
    assert FROZEN["fee_per_assignment"] == 0.0


def test_real_money_flag_refuses_before_any_network(monkeypatch):
    """A8 (rescoped): real-money mode requires a wired position reconciler and
    order code; neither exists. `real_money: true` must refuse with exit 3
    BEFORE building a client or pulling anything -- a refused run must not
    burn API quota or half-execute. Alert sent so the misconfiguration is
    loud, not just a silent nonzero in the journal."""
    import datetime as dt
    import sys
    import types
    from live import run_daily
    from live.config import DEFAULTS

    class _FrozenDT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 8, 3, 17, 30, tzinfo=tz)   # past the A11 gate

    monkeypatch.setattr(run_daily.dt, "datetime", _FrozenDT)
    fake = types.ModuleType("schwab_client")
    fake.get_client = lambda: (_ for _ in ()).throw(
        AssertionError("A8: refused run must never build a client"))
    monkeypatch.setitem(sys.modules, "schwab_client", fake)
    monkeypatch.setattr(run_daily, "load_run_config",
                        lambda: {**DEFAULTS, "real_money": True})
    alerts = []
    monkeypatch.setattr(run_daily, "send_alert",
                        lambda subject, body: alerts.append(subject) or True)
    monkeypatch.setattr(sys, "argv", ["run_daily.py"])
    rc = run_daily.main()
    assert rc == 3, "A8: real_money=true must refuse with exit 3"
    assert alerts, "A8: the refusal must alert, not just exit"


def test_clock_gate_boundary_is_seventeen(monkeypatch):
    """Skeptic F1: 16:0x closes are preliminary; the shell's old constant was
    'after 5pm ET (data settled)'. 16:59 refused, 17:00 reaches the client
    build (sentinel)."""
    import datetime as dt
    import sys
    import types
    from live import run_daily

    def frozen(h, m):
        class _F(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return dt.datetime(2026, 7, 24, h, m, tzinfo=tz)
        return _F

    fake = types.ModuleType("schwab_client")

    class _Sentinel(Exception):
        pass

    fake.get_client = lambda: (_ for _ in ()).throw(_Sentinel())
    monkeypatch.setitem(sys.modules, "schwab_client", fake)
    monkeypatch.setattr(sys, "argv", ["run_daily.py"])

    monkeypatch.setattr(run_daily.dt, "datetime", frozen(16, 59))
    assert run_daily.main() == 2
    monkeypatch.setattr(run_daily.dt, "datetime", frozen(17, 0))
    try:
        run_daily.main()
        raise AssertionError("17:00 must pass the gate (sentinel expected)")
    except _Sentinel:
        pass


def test_every_warning_kind_reaches_the_log(tmp_path, capsys):
    """A14: paper_step surfaced only the gate/covered-call reasons; every
    OTHER warning (expiry_unsettleable above all -- a leg past expiry that
    cannot settle because its close is missing) was thrown away. A leg could
    sit unsettled forever and the log would say nothing."""
    from live.run_daily import paper_step
    from src.engine_v2.options.chain import Contract

    class _M:
        universe = ["TMO"]
        _obs = OBS

        def chain(self, tk, d):
            return None

        def spot(self, tk, d, fb):
            return None if d == pd.Timestamp("2026-07-10") else 500.0

        def settle_price(self, tk, expiry):
            return None

        def regime_row(self, tk, day):
            return None

        def eligible(self, tk, day):
            return True

    c = Contract("TMO", pd.Timestamp("2026-07-10"), 512.5, "P")   # expired, unsettleable
    state = PortfolioState(cash=100_000.0, positions=[{
        "ticker": "TMO", "shares": 0, "phase": "PUT", "basis": None,
        "premium": 900.0, "campaign": 1, "last_spot": 500.0,
        "short": {"contract": c, "contracts": 1, "credit": 9.0,
                  "last_mid": 9.0}}])
    cfg = WheelConfig(ticker="TMO", put_delta=0.30, call_delta=0.50,
                      target_dte=11, take_profit_pct=0.60,
                      starting_capital=100_000.0, call_min_strike="basis")
    r = paper_step(state, _M(), cfg, 1, str(tmp_path / "t.jsonl"),
                   str(tmp_path / "s.json"))
    assert any(w[1] == "expiry_unsettleable" for w in r.warnings)
    out = capsys.readouterr().out
    assert "expiry_unsettleable" in out, \
        "A14: an unsettleable expiry must be visible in the run log"


def test_collect_unsettled_names_the_leg_and_the_lateness():
    from live.run_daily import collect_unsettled
    from src.engine_v2.options.chain import Contract
    c = Contract("TMO", pd.Timestamp("2026-07-10"), 512.5, "P")
    w = [(pd.Timestamp("2026-07-17"), "expiry_unsettleable", c),
         (pd.Timestamp("2026-07-15"), "expiry_unsettleable", c),   # earlier sighting
         (pd.Timestamp("2026-07-17"), "route_state_unknown", "GDX")]
    got = collect_unsettled(w)
    assert got == {"TMO 512.5P 2026-07-10": 7}   # max lateness wins


def test_holiday_note_names_day_and_warns_about_stale_feeds(monkeypatch):
    """D2 (owner 2026-08-01): a weekday classified as a holiday must EMAIL,
    not just print -- a stale-but-well-formed feed on a real trading day is
    indistinguishable from a holiday to the bot, and before this note the
    day was silently lost (the 2026-07-24 class). ~9 benign emails/yr."""
    import live.run_daily as rd
    calls = []
    monkeypatch.setattr(rd, "send_alert",
                        lambda s, b, **kw: calls.append((s, b)) or True)
    rd.holiday_note(pd.Timestamp("2026-11-26"))
    assert len(calls) == 1
    subject, body = calls[0]
    assert "2026-11-26" in subject
    assert "holiday" in (subject + body).lower()
    assert "stale" in body.lower()


def test_holiday_note_quiet_on_smoke_and_weekends(monkeypatch):
    """Group skeptic F5: a --smoke connectivity check and a manual weekend
    run both hit the holiday paths -- neither is a real holiday signal and
    neither may email."""
    import live.run_daily as rd
    calls = []
    monkeypatch.setattr(rd, "send_alert",
                        lambda s, b, **kw: calls.append(s) or True)
    rd.holiday_note(pd.Timestamp("2026-11-26"), smoke=True)     # Thu, smoke
    rd.holiday_note(pd.Timestamp("2026-07-25"))                 # Saturday
    assert calls == []
    rd.holiday_note(pd.Timestamp("2026-11-26"))                 # real weekday
    assert len(calls) == 1


def test_smoke_pull_failure_touches_no_real_ledger_and_sends_no_mail(tmp_path, monkeypatch):
    """A23: --smoke is a throwaway connectivity check, but its zombie path
    called the REAL send_alert and appended to the REAL gaps.jsonl. A dead
    feed during a casual smoke test emailed the owner and permanently wrote
    a pull_failure gap for a day the real 17:00 run may go on to complete.
    State/trades/snapshots already isolate to _smoke; gaps+alerts must too."""
    import sys
    import types
    import live.data as data
    from live import run_daily

    monkeypatch.setenv("WHEELBOT_STATE_DIR", str(tmp_path))

    def dead_feed(c, tk):
        raise RuntimeError("connectivity check: feed down")
    monkeypatch.setattr(data, "daily_closes", dead_feed)

    alerts, gaps = [], []
    monkeypatch.setattr(run_daily, "send_alert",
                        lambda *a, **k: alerts.append(a) or True)
    monkeypatch.setattr(run_daily, "append_gap",
                        lambda date, reason, **kw: gaps.append((str(date), reason)) or True)

    fake = types.ModuleType("schwab_client")
    fake.get_client = lambda: object()
    monkeypatch.setitem(sys.modules, "schwab_client", fake)
    monkeypatch.setattr(sys, "argv", ["run_daily.py", "--smoke"])

    rc = run_daily.main()
    assert rc == 1, "a dead feed is still a FAILED smoke run (exit contract kept)"
    assert gaps == [], "A23: --smoke wrote the real gaps ledger"
    assert alerts == [], "A23: --smoke sent a real alert"


def test_holiday_with_dead_quote_endpoint_is_a_holiday_not_a_failed_run(tmp_path, monkeypatch):
    """A21c: held_marks_failed was judged BEFORE the holiday classification.
    On a market holiday Schwab's quote endpoint can answer for nothing (or
    error), so the run exited 1 and the tick retry-alerted 'daily run FAILED
    (held-leg marks)' all evening -- for a day with no session, which every
    later gate classifies as exit-0 holiday. Session classification must come
    first: no session means there are no marks to fail."""
    import datetime as dt
    import sys
    import types
    from zoneinfo import ZoneInfo
    import live.data as data
    from live import run_daily

    monkeypatch.setenv("WHEELBOT_STATE_DIR", str(tmp_path))
    # closes exist but carry NO bar for today -> is_trading_day False (holiday)
    obs = pd.Timestamp(dt.datetime.now(ZoneInfo("America/New_York")).date())
    stale = pd.Series([50.0, 50.5], index=pd.bdate_range(end=obs - pd.Timedelta(days=3), periods=2))
    monkeypatch.setattr(run_daily, "UNIVERSE", ["GDX"])
    monkeypatch.setattr(data, "daily_closes", lambda c, tk: stale)

    # held legs requested, endpoint answered for NOTHING (the B4 wholesale shape)
    monkeypatch.setattr(run_daily, "merge_held_legs",
                        lambda *a, **k: {"requested": 4, "merged": 0,
                                         "unquoted": ["RIG 4.5P"], "no_chain": [],
                                         "error": "HTTP 503", "answered": 0})

    alerts, gaps = [], []
    monkeypatch.setattr(run_daily, "send_alert",
                        lambda *a, **k: alerts.append(a) or True)
    monkeypatch.setattr(run_daily, "append_gap",
                        lambda date, reason, **kw: gaps.append((str(date), reason)) or True)

    fake = types.ModuleType("schwab_client")
    fake.get_client = lambda: object()
    monkeypatch.setitem(sys.modules, "schwab_client", fake)
    monkeypatch.setattr(sys, "argv", ["run_daily.py", "--force"])

    rc = run_daily.main()
    assert rc == 0, "A21c: a holiday must exit 0 even when the quote endpoint is dead"
    assert not any("held-leg marks" in a[0] for a in alerts), \
        "A21c: FAILED-held-marks alert fired on a day with no session"
    assert gaps == [], "a holiday is not a gap"


def test_trading_day_dead_quote_endpoint_still_fails_loud(tmp_path, monkeypatch, capsys):
    """A21c positive path (and the B4 wiring pin the Group B batch declared
    missing): on a REAL trading day a wholesale held-marks failure must still
    refuse the run BEFORE any account steps -- exit 1, loud, retried."""
    import datetime as dt
    import sys
    import types
    from zoneinfo import ZoneInfo
    import live.data as data
    from live import run_daily

    monkeypatch.setenv("WHEELBOT_STATE_DIR", str(tmp_path))
    obs = pd.Timestamp(dt.datetime.now(ZoneInfo("America/New_York")).date())
    fresh = pd.Series([50.0, 50.5],
                      index=[obs - pd.Timedelta(days=1), obs])   # bar TODAY
    monkeypatch.setattr(data, "daily_closes", lambda c, tk: fresh)
    # E4 marks GDX held-for-pull on --smoke; give it a chain so the zombie
    # gate stays quiet and the flow reaches the held-marks judgment
    exp = obs + pd.Timedelta(days=11)
    chain = pd.DataFrame(
        [[obs, exp, 11, 45.0, "P", 1.0, 1.1, 1.05, 1.05, -0.3, 0.2, 50.0]],
        columns=["date", "expiry", "dte", "strike", "right", "bid", "ask",
                 "mid", "close", "delta", "iv", "underlying"])
    monkeypatch.setattr(data, "chain_frame", lambda *a, **k: chain)
    monkeypatch.setattr(run_daily, "merge_held_legs",
                        lambda *a, **k: {"requested": 4, "merged": 0,
                                         "unquoted": ["RIG 4.5P"], "no_chain": [],
                                         "error": "HTTP 503", "answered": 0})
    alerts = []
    monkeypatch.setattr(run_daily, "send_alert",
                        lambda *a, **k: alerts.append(a) or True)
    fake = types.ModuleType("schwab_client")
    fake.get_client = lambda: object()
    monkeypatch.setitem(sys.modules, "schwab_client", fake)
    # --smoke: throwaway store, skips the snapshot gates (a complete snapshot
    # fixture is not this test's subject) while exercising the same block
    monkeypatch.setattr(sys, "argv", ["run_daily.py", "--smoke"])

    rc = run_daily.main()
    out = capsys.readouterr().out
    assert rc == 1, "a dead quote endpoint on a trading day is a FAILED run"
    assert "HELD MARKS FAILED" in out
    assert "paper day" not in out, "no account may step past dead held marks"
    assert alerts == [], "smoke keeps alert isolation even on this path (A23)"


def test_smoke_run_probes_the_held_leg_merge(tmp_path, monkeypatch):
    """E4: --smoke held zero positions, so a connectivity check could pass
    with the entire held-leg quote path (merge, splice, unquoted disclosure)
    broken. The smoke run must inject one synthetic probe leg -- derived from
    the pulled GDX chain, just below its bottom strike -- into the MERGE call
    only (never into account state), so the quote endpoint and splice wiring
    are exercised every smoke run."""
    import datetime as dt
    import sys
    import types
    from zoneinfo import ZoneInfo
    import live.data as data
    from live import run_daily

    monkeypatch.setenv("WHEELBOT_STATE_DIR", str(tmp_path))
    obs = pd.Timestamp(dt.datetime.now(ZoneInfo("America/New_York")).date())
    fresh = pd.Series([50.0, 50.5], index=[obs - pd.Timedelta(days=1), obs])
    monkeypatch.setattr(data, "daily_closes", lambda c, tk: fresh)

    exp = obs + pd.Timedelta(days=11)
    chain = pd.DataFrame(
        [[obs, exp, 11, float(k), "P", 1.0, 1.1, 1.05, 1.05, -0.3, 0.2, 50.0]
         for k in (44, 45, 46, 47)],
        columns=["date", "expiry", "dte", "strike", "right", "bid", "ask",
                 "mid", "close", "delta", "iv", "underlying"])
    monkeypatch.setattr(data, "chain_frame", lambda *a, **k: chain)
    monkeypatch.setattr(run_daily, "send_alert", lambda *a, **k: True)

    seen = []

    def rec_merge(market, client, position_lists, obs_):
        seen.append([p for lst in position_lists for p in lst])
        return {"requested": 0, "merged": 0, "unquoted": [], "no_chain": [],
                "error": None, "answered": 0}
    monkeypatch.setattr(run_daily, "merge_held_legs", rec_merge)

    fake = types.ModuleType("schwab_client")
    fake.get_client = lambda: object()
    monkeypatch.setitem(sys.modules, "schwab_client", fake)
    monkeypatch.setattr(sys, "argv", ["run_daily.py", "--smoke"])
    run_daily.main()

    assert seen, "merge_held_legs never ran"
    probes = [p for p in seen[0] if p.get("smoke_probe")]
    assert probes, "E4: no probe leg reached the merge on --smoke"
    c = probes[0]["short"]["contract"]
    assert probes[0]["ticker"] == "GDX"
    assert float(c["strike"]) < 44.0, \
        "the probe must sit BELOW the surveyed bottom strike (splice path)"


def test_frozen_leg_surfaces_in_log_and_alert_wiring_exists(tmp_path, capsys):
    """A10: a ca-frozen day must be loud twice over -- the generic warning
    printer names it in the run log (behavioral), and main() aggregates
    ca_confirmed_frozen across accounts into ONE daily FROZEN alert (source
    pin -- the main() harness cost is the WHEELBOT_STATE_DIR trap, same
    declared trade as the C13 lint)."""
    from live.run_daily import paper_step
    from src.engine_v2.options.chain import Contract

    class _M:
        universe = ["XYZ"]
        _obs = OBS

        def chain(self, tk, d):
            return None

        def spot(self, tk, d, fb):
            return 52.5

        def settle_price(self, tk, expiry):
            return 52.5

        def prior_close(self, tk, d):
            return 52.0                      # stored 104 -> restated x0.5

        def regime_row(self, tk, day):
            return None

        def eligible(self, tk, day):
            return True

    c = Contract("XYZ", OBS + pd.Timedelta(days=7), 100.0, "P")
    state = PortfolioState(cash=100_000.0, positions=[{
        "ticker": "XYZ", "shares": 0, "phase": "PUT", "basis": None,
        "premium": 0.0, "campaign": 1, "last_spot": 104.0,
        "short": {"contract": c, "contracts": 9, "credit": 2.0,
                  "last_mid": 2.0}}], prev_d=OBS - pd.Timedelta(days=1))
    cfg = WheelConfig(ticker="XYZ", put_delta=0.30, call_delta=0.50,
                      target_dte=11, take_profit_pct=0.60,
                      starting_capital=100_000.0, call_min_strike="basis")
    r = paper_step(state, _M(), cfg, 1, str(tmp_path / "t.jsonl"),
                   str(tmp_path / "s.json"))
    assert any(w[1] == "ca_confirmed_frozen" for w in r.warnings)
    assert "ca_confirmed_frozen" in capsys.readouterr().out, \
        "A10: a frozen leg must be visible in the run log"

    import inspect
    import re
    import live.run_daily as rd
    src = inspect.getsource(rd)
    assert re.search(r'frozen_all \|= \{str\(w\[2\]\) for w in r\.warnings'
                     r'[\s\S]{0,80}"ca_confirmed_frozen"', src), \
        "A10: main() no longer aggregates frozen legs across accounts"
    assert re.search(r'if frozen_all:[\s\S]{0,400}FROZEN', src), \
        "A10: the daily FROZEN alert wiring is gone"
