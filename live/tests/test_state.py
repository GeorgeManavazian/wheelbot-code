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
