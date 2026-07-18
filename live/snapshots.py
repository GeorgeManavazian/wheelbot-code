"""Daily portfolio snapshots for the live dashboard (sub-project B4). After each
paper day, append one JSON line capturing equity/cash/positions so the dashboard
can draw an equity curve + position history over time. Data-only, append-only."""
from __future__ import annotations
import json
import os
import pandas as pd


def snapshot(state, equity: float, day) -> dict:
    """One day's portfolio snapshot from the post-step state + marked equity."""
    positions = []
    for p in state.positions:
        sh = p["short"]
        positions.append({
            "ticker": p["ticker"], "phase": p["phase"], "shares": p["shares"],
            "campaign": p["campaign"], "basis": p["basis"], "premium": p["premium"],
            "short": None if sh is None else {
                "strike": sh["contract"].strike,
                "expiry": pd.Timestamp(sh["contract"].expiry).isoformat(),
                "right": sh["contract"].right, "contracts": sh["contracts"],
                "credit": sh["credit"], "last_mid": sh["last_mid"]},
        })
    return {
        "date": pd.Timestamp(day).normalize().isoformat(),
        "equity": float(equity), "cash": float(state.cash),
        "n_positions": len(state.positions),
        "days_flat": state.days_flat,
        "positions": positions,
    }


def append_snapshot(path, snap) -> None:
    """Append one snapshot as a JSON line (append-only history)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(snap) + "\n")


def load_snapshots(path) -> list:
    """Read the snapshot history (oldest first). Empty list if absent."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(x) for x in f if x.strip()]
