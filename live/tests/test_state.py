import os
import pandas as pd
from src.engine_v2.options.chain import Contract
from src.engine_v2.options.portfolio import PortfolioState
from live.state import state_to_dict, state_from_dict, save_state, load_state


def _state():
    put = Contract("GDX", pd.Timestamp("2026-01-15"), 30.0, "P")
    open_put = {"ticker": "GDX", "shares": 0, "phase": "PUT", "basis": None,
                "premium": 99.0, "campaign": 1, "last_spot": 33.0,
                "short": {"contract": put, "contracts": 1, "credit": 1.0, "last_mid": 0.9}}
    assigned = {"ticker": "SLV", "shares": 100, "phase": "CALL", "basis": 25.0,
                "premium": 40.0, "campaign": 2, "last_spot": 24.0, "short": None}
    return PortfolioState(cash=95_000.0, positions=[open_put, assigned], campaign=2,
                          days_flat=3, days_shares_uncovered=1,
                          prev_d=pd.Timestamp("2026-07-16"))


def test_round_trip_preserves_everything():
    s = _state()
    s2 = state_from_dict(state_to_dict(s))
    assert s2.cash == s.cash and s2.campaign == s.campaign
    assert s2.days_flat == 3 and s2.days_shares_uncovered == 1
    assert s2.prev_d == pd.Timestamp("2026-07-16")
    # open-put campaign: nested Contract survives
    c = s2.positions[0]["short"]["contract"]
    assert isinstance(c, Contract)
    assert (c.root, c.strike, c.right) == ("GDX", 30.0, "P")
    assert c.expiry == pd.Timestamp("2026-01-15")
    assert s2.positions[0]["short"]["credit"] == 1.0
    # assigned-shares campaign: short is None, shares/basis survive
    assert s2.positions[1]["short"] is None
    assert s2.positions[1]["shares"] == 100 and s2.positions[1]["basis"] == 25.0


def test_save_load_atomic_round_trip(tmp_path):
    p = str(tmp_path / "sub" / "state.json")   # nested dir must be created
    save_state(_state(), p)
    assert os.path.exists(p)
    loaded = load_state(p)
    assert loaded.cash == 95_000.0 and len(loaded.positions) == 2


def test_load_absent_is_none(tmp_path):
    assert load_state(str(tmp_path / "nope.json")) is None


def test_save_keeps_prev_backup(tmp_path):
    p = str(tmp_path / "state.json")
    save_state(_state(), p)
    import os
    assert not os.path.exists(p + ".prev")   # first write: no prior
    s2 = _state(); s2.cash = 1.0
    save_state(s2, p)
    assert os.path.exists(p + ".prev")        # second write backs up the first
    assert load_state(p + ".prev").cash == 95_000.0   # prev holds the OLD value
    assert load_state(p).cash == 1.0


def test_last_ask_round_trips(tmp_path):
    """Equity is marked off last_ask (owner decision B). The serializer
    whitelists keys, so a field it does not name is silently dropped on save --
    which would send every leg back to its stale mid on the next run, i.e.
    restore the exact defect the change was made to remove."""
    from live.state import save_state, load_state
    put = Contract("GDX", pd.Timestamp("2026-08-21"), 30.0, "P")
    pos = {"ticker": "GDX", "shares": 0, "phase": "PUT", "basis": None,
           "premium": 90.0, "campaign": 1, "last_spot": 35.0,
           "short": {"contract": put, "contracts": 1, "credit": 1.0,
                     "last_mid": 1.10, "last_ask": 1.30}}
    p = str(tmp_path / "state.json")
    save_state(PortfolioState(cash=100_000.0, positions=[pos]), p)
    back = load_state(p)
    assert back.positions[0]["short"]["last_ask"] == 1.30
    assert back.positions[0]["short"]["last_mid"] == 1.10


def test_a_leg_saved_before_last_ask_existed_still_loads(tmp_path):
    """The 25 live accounts hold 43 legs written under the old schema."""
    import json
    from live.state import load_state
    p = str(tmp_path / "state.json")
    with open(p, "w") as f:
        json.dump({"cash": 100.0, "campaign": 1, "days_flat": 0,
                   "days_shares_uncovered": 0, "prev_d": None,
                   "positions": [{"ticker": "GDX", "shares": 0, "phase": "PUT",
                                  "basis": None, "premium": 90.0, "campaign": 1,
                                  "last_spot": 35.0,
                                  "short": {"contract": {"root": "GDX",
                                                         "expiry": "2026-08-21T00:00:00",
                                                         "strike": 30.0, "right": "P"},
                                            "contracts": 1, "credit": 1.0,
                                            "last_mid": 1.10}}]}, f)
    back = load_state(p)
    assert "last_ask" not in back.positions[0]["short"]
    assert back.positions[0]["short"]["last_mid"] == 1.10


def test_a17_partial_fill_fields_round_trip(tmp_path):
    """A17 capacity: opened_contracts (original leg size after a partial fill)
    and working_order (an order a fill model has working) must survive the
    round trip -- the serializer whitelists keys, so an unnamed field is
    silently dropped and the partial state would evaporate on the next run."""
    from live.state import save_state, load_state
    put = Contract("GDX", pd.Timestamp("2026-08-21"), 30.0, "P")
    pos = {"ticker": "GDX", "shares": 0, "phase": "PUT", "basis": None,
           "premium": 90.0, "campaign": 1, "last_spot": 35.0,
           "working_order": {"side": "BTC", "contracts": 10, "filled": 4,
                             "limit": 0.40, "placed_at": "2026-07-21T10:00:00"},
           "short": {"contract": put, "contracts": 6, "credit": 1.0,
                     "last_mid": 1.10, "opened_contracts": 10}}
    p = str(tmp_path / "state.json")
    save_state(PortfolioState(cash=100_000.0, positions=[pos]), p)
    back = load_state(p)
    assert back.positions[0]["short"]["opened_contracts"] == 10
    assert back.positions[0]["short"]["contracts"] == 6
    assert back.positions[0]["working_order"]["filled"] == 4


def test_a17_fields_absent_stay_absent(tmp_path):
    """Default instant-fill path: no partial ever happened, so the saved file
    must be byte-compatible with the pre-A17 schema (no new keys invented)."""
    import json
    from live.state import save_state
    put = Contract("GDX", pd.Timestamp("2026-08-21"), 30.0, "P")
    pos = {"ticker": "GDX", "shares": 0, "phase": "PUT", "basis": None,
           "premium": 90.0, "campaign": 1, "last_spot": 35.0,
           "short": {"contract": put, "contracts": 1, "credit": 1.0,
                     "last_mid": 1.10}}
    p = str(tmp_path / "state.json")
    save_state(PortfolioState(cash=100_000.0, positions=[pos]), p)
    raw = json.load(open(p))
    assert "working_order" not in raw["positions"][0]
    assert "opened_contracts" not in raw["positions"][0]["short"]


# ---- D11: dated day-boundary backup + durable rename ----

def test_first_save_of_day_keeps_day_boundary_backup(tmp_path):
    """.prev is refreshed on EVERY save (and run_intraday saves on every TP
    close), so by evening it is minutes old -- a valid-but-wrong EOD write
    destroyed the last good day-boundary state. The FIRST save of a day must
    keep the state it found as a dated backup that later saves never touch."""
    import glob
    from live.state import save_state, load_state, PortfolioState
    p = str(tmp_path / "state.json")
    save_state(PortfolioState(cash=111.0, positions=[]), p)
    save_state(PortfolioState(cash=222.0, positions=[]), p)   # first save "today" over 111
    save_state(PortfolioState(cash=333.0, positions=[]), p)   # intraday churn
    baks = glob.glob(p + ".bak-*")
    assert baks, "no day-boundary backup written"
    assert load_state(baks[0]).cash == 111.0, \
        "backup must hold the state the day STARTED from, not intraday churn"
    assert load_state(p + ".prev").cash == 222.0   # .prev semantics unchanged


def test_backups_older_than_seven_days_are_pruned(tmp_path):
    import glob
    from live.state import save_state, PortfolioState
    p = str(tmp_path / "state.json")
    stale = p + ".bak-2020-01-01"
    open(stale, "w").write("{}")
    save_state(PortfolioState(cash=1.0, positions=[]), p)
    save_state(PortfolioState(cash=2.0, positions=[]), p)
    assert not os.path.exists(stale)


def test_directory_fsync_after_rename(tmp_path, monkeypatch):
    """Mechanism test (declared): os.replace's rename is not durable across
    power loss without an fsync on the DIRECTORY fd."""
    from live import state as st
    p = str(tmp_path / "state.json")
    fsynced = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: fsynced.append(fd) or real_fsync(fd))
    dir_fds = []
    real_open = os.open

    def spy_open(path, flags, *a, **kw):
        fd = real_open(path, flags, *a, **kw)
        if os.path.isdir(path):
            dir_fds.append(fd)
        return fd
    monkeypatch.setattr(os, "open", spy_open)
    st.save_state(st.PortfolioState(cash=1.0, positions=[]), p)
    assert any(fd in fsynced for fd in dir_fds), \
        "the state directory was never fsynced after the rename"


def test_ca_freeze_and_watch_survive_the_round_trip(tmp_path):
    """A10: `_POS_SCALARS` silently DROPS unlisted keys on save (the exact
    trap the A21 analyst flagged) -- a freeze that evaporates on reload
    un-freezes a corrupted position overnight. Optional-when-absent like
    last_ask: files without the keys stay byte-identical."""
    from src.engine_v2.options.portfolio import PortfolioState
    from live.state import save_state, load_state
    p = str(tmp_path / "state.json")
    pos = {"ticker": "XYZ", "shares": 0, "phase": "PUT", "basis": None,
           "premium": 0.0, "campaign": 1, "last_spot": 104.0,
           "ca_frozen": {"date": "2026-08-01", "stored_spot": 104.0,
                         "ratio": 0.5, "restated_close_of": "2026-07-31"},
           "ca_watch": {"date": "2026-07-31", "spot": 104.0},
           "short": None}
    save_state(PortfolioState(cash=1.0, positions=[pos]), p)
    got = load_state(p).positions[0]
    assert got.get("ca_frozen") == pos["ca_frozen"], \
        "A10: the freeze evaporated on the state round trip"
    assert got.get("ca_watch") == pos["ca_watch"]


def test_positions_without_ca_keys_stay_key_identical(tmp_path):
    from src.engine_v2.options.portfolio import PortfolioState
    from live.state import save_state, load_state
    p = str(tmp_path / "state.json")
    pos = {"ticker": "XYZ", "shares": 0, "phase": "PUT", "basis": None,
           "premium": 0.0, "campaign": 1, "last_spot": 104.0, "short": None}
    save_state(PortfolioState(cash=1.0, positions=[pos]), p)
    got = load_state(p).positions[0]
    assert "ca_frozen" not in got and "ca_watch" not in got, \
        "absent CA keys must stay absent (legacy files byte-identical)"
    assert "recon_frozen" not in got, \
        "A8: absent recon key must stay absent (legacy files byte-identical)"


def test_recon_freeze_survives_the_round_trip(tmp_path):
    """A8: `recon_frozen` (broker-vs-state divergence freeze, real-money mode)
    uses the exact A10 lifecycle -- sticky until a human clears it. Landing
    the round-trip key now, before any engine wiring, means state files never
    need a migration when real-money mode is built."""
    from src.engine_v2.options.portfolio import PortfolioState
    from live.state import save_state, load_state
    p = str(tmp_path / "state.json")
    pos = {"ticker": "XYZ", "shares": 0, "phase": "PUT", "basis": None,
           "premium": 0.0, "campaign": 1, "last_spot": 104.0,
           "recon_frozen": {"date": "2026-08-02",
                            "kind": "early_put_assignment",
                            "expected": -3, "observed": 0},
           "short": None}
    save_state(PortfolioState(cash=1.0, positions=[pos]), p)
    got = load_state(p).positions[0]
    assert got.get("recon_frozen") == pos["recon_frozen"], \
        "A8: the recon freeze evaporated on the state round trip"
