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
