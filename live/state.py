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
    sh = p["short"]
    d["short"] = None if sh is None else {
        "contract": _contract_to_dict(sh["contract"]),
        "contracts": sh["contracts"], "credit": sh["credit"], "last_mid": sh["last_mid"]}
    return d


def _pos_from_dict(d):
    p = {k: d[k] for k in _POS_SCALARS}
    sh = d["short"]
    p["short"] = None if sh is None else {
        "contract": _contract_from_dict(sh["contract"]),
        "contracts": sh["contracts"], "credit": sh["credit"], "last_mid": sh["last_mid"]}
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
    d = state_to_dict(state)
    dirn = os.path.dirname(path) or "."
    os.makedirs(dirn, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dirn, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, path)


def load_state(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return state_from_dict(json.load(f))
