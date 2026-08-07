"""Persisted daily IV observations. Data-only; no order code.

One JSON file per trading day, keyed by the ET observation date, holding one
record per (ticker, grid cell). The git state mirror therefore sees one NEW
file per day and never a modified one -- the cheapest diff available, and the
reason this shape was chosen over per-ticker files.

`load_iv_day` returns None -- never a stale list -- when the file is absent,
unreadable, or stamped with a different obs date. Serving another day's
observations as today's would append quotes measured elsewhere into a series
whose entire meaning is a ticker compared against its own past. Same doctrine
as chain_store.load_chain_snapshot.
"""
from __future__ import annotations

import json
import os

import pandas as pd

from live.paths import in_state


def iv_path(obs) -> str:
    return in_state("iv", f"{pd.Timestamp(obs).date()}.json")


def save_iv_day(obs, records: list, pulled_at: str, path=None) -> str:
    """Persist `records` for `obs`. Atomic (tmp + rename): a reader must never
    see a half-written file from a pass that died mid-dump."""
    obs = pd.Timestamp(obs).normalize()
    path = path or iv_path(obs)
    payload = {"obs": str(obs.date()), "pulled_at": pulled_at,
               "records": list(records)}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)
    return path


def load_iv_day(obs, path=None):
    """The records for exactly `obs`, or None when no usable file exists.

    An EMPTY list is a real answer ("pulled fine, nothing selectable") and is
    deliberately distinguishable from None ("no usable file")."""
    obs = pd.Timestamp(obs).normalize()
    path = path or iv_path(obs)
    try:
        with open(path) as f:
            payload = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("obs") != str(obs.date()):
        return None
    recs = payload.get("records")
    if not isinstance(recs, list):
        return None
    return recs


def tickers_done(records: list) -> set:
    """Tickers already recorded today. Resume is keyed on the ticker, not the
    grid cell: one API call produces every cell, so a ticker is either done or
    not."""
    return {r["ticker"] for r in records if isinstance(r, dict) and "ticker" in r}
