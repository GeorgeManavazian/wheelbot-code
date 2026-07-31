"""PortfolioState <-> JSON. Positions carry nested Contract dataclasses, so
(de)serialization is explicit. save_state is atomic (temp + os.replace) so a
crash mid-write can't corrupt a real portfolio."""
from __future__ import annotations
import json
import os
import tempfile
import pandas as pd
from src.engine_v2.options.chain import Contract
from src.engine_v2.options.portfolio import PortfolioState

_POS_SCALARS = ("ticker", "shares", "phase", "basis", "premium", "campaign", "last_spot")


def _contract_to_dict(c):
    return {"root": c.root, "expiry": pd.Timestamp(c.expiry).isoformat(),
            "strike": c.strike, "right": c.right}


def _contract_from_dict(d):
    return Contract(d["root"], pd.Timestamp(d["expiry"]), d["strike"], d["right"])


def _pos_to_dict(p):
    d = {k: p[k] for k in _POS_SCALARS}
    # A17: an order a fill model has working (JSON-plain dict). Absent == none;
    # written only when present so existing files and default-path saves are
    # byte-identical to before the field existed.
    if p.get("working_order") is not None:
        d["working_order"] = p["working_order"]
    sh = p["short"]
    if sh is None:
        d["short"] = None
        return d
    out = {"contract": _contract_to_dict(sh["contract"]),
           "contracts": sh["contracts"], "credit": sh["credit"],
           "last_mid": sh["last_mid"]}
    # last_ask is what equity is marked from (owner decision B, 2026-07-31). It
    # is optional ONLY because legs opened before that change have none; once a
    # leg has one it must survive the round trip, or every run would reload the
    # stale mid and silently restore the defect.
    if "last_ask" in sh:
        out["last_ask"] = sh["last_ask"]
    # A17: partial-fill capacity, optional for the same reason as last_ask --
    # every existing state file lacks it and must keep loading. Original leg
    # size; absent == equals `contracts`.
    if "opened_contracts" in sh:
        out["opened_contracts"] = sh["opened_contracts"]
    d["short"] = out
    return d


def _pos_from_dict(d):
    p = {k: d[k] for k in _POS_SCALARS}
    if d.get("working_order") is not None:
        p["working_order"] = d["working_order"]
    sh = d["short"]
    if sh is None:
        p["short"] = None
        return p
    out = {"contract": _contract_from_dict(sh["contract"]),
           "contracts": sh["contracts"], "credit": sh["credit"],
           "last_mid": sh["last_mid"]}
    if "last_ask" in sh:
        out["last_ask"] = sh["last_ask"]
    if "opened_contracts" in sh:
        out["opened_contracts"] = sh["opened_contracts"]
    p["short"] = out
    return p


def state_to_dict(state) -> dict:
    return {
        "cash": state.cash, "campaign": state.campaign,
        "days_flat": state.days_flat,
        "days_shares_uncovered": state.days_shares_uncovered,
        "prev_d": None if state.prev_d is None else pd.Timestamp(state.prev_d).isoformat(),
        "positions": [_pos_to_dict(p) for p in state.positions],
    }


def state_from_dict(d) -> PortfolioState:
    return PortfolioState(
        cash=d["cash"], positions=[_pos_from_dict(p) for p in d["positions"]],
        campaign=d["campaign"], days_flat=d["days_flat"],
        days_shares_uncovered=d["days_shares_uncovered"],
        prev_d=None if d["prev_d"] is None else pd.Timestamp(d["prev_d"]))


def save_state(state, path):
    """Atomic + durable + one-level backup. temp-write -> fsync -> keep the
    prior state.json as .prev (recovery from a valid-but-wrong write) ->
    os.replace. A crash or power loss can't leave a half-written or truncated
    state.json, and yesterday's state is always one file away."""
    import shutil
    d = state_to_dict(state)
    dirn = os.path.dirname(path) or "."
    os.makedirs(dirn, exist_ok=True)
    if os.path.exists(path):
        shutil.copy2(path, path + ".prev")
    fd, tmp = tempfile.mkstemp(dir=dirn, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(d, f, indent=2)
        f.flush()
        os.fsync(f.fileno())     # durable content before the atomic rename
    os.replace(tmp, path)


def load_state(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return state_from_dict(json.load(f))
