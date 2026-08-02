"""Persisted RTH option-chain snapshot (A16). Data-only; no order code.

The daily decision runs at 17:00 ET, after the options close, where the book
is 3-4x wider than anything tradeable (median rel-spread 29.8% post-close vs
7.4% intraday). So the chain pull and the decision are SPLIT: a runner inside
regular hours (live/run_chain_snapshot.py) pulls the chains and saves them
here; run_daily.py loads them back instead of touching the live endpoint.

The store is one JSON file per trading day, keyed by the ET observation date.
`load_chain_snapshot` returns None -- never a stale dict -- when the file is
absent, unreadable, or stamped with a different obs date, because serving
yesterday's chains as today's is exactly the class of silent staleness this
repair exists to remove (2026-07-24 stepped a day against 3-6 day old marks).
"""
from __future__ import annotations

import json
import os

import pandas as pd

from live.paths import in_state
from live.data import _CHAIN_COLS


def snapshot_path(obs) -> str:
    return in_state("chains", f"{pd.Timestamp(obs).date()}.json")


def save_chain_snapshot(obs, chains: dict, pulled_at: str, path=None) -> str:
    """Persist {ticker -> chain DataFrame} for `obs`. Atomic (tmp + rename):
    the 17:00 reader must never see a half-written file from a snapshot pass
    that died mid-dump."""
    obs = pd.Timestamp(obs).normalize()
    path = path or snapshot_path(obs)
    ser = {}
    for tk, df in chains.items():
        d = df.copy()
        d["date"] = d["date"].astype(str)
        d["expiry"] = d["expiry"].astype(str)
        ser[tk] = d.to_dict("records")
    payload = {"obs": str(obs.date()), "pulled_at": pulled_at, "chains": ser}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)
    return path


def load_chain_snapshot(obs, path=None):
    """{ticker -> chain DataFrame} for `obs`, or None when no usable snapshot
    exists for exactly that date. Column shapes match chain_from_json so the
    engine cannot tell a loaded chain from a freshly pulled one. (One declared
    exception: an EMPTY frame round-trips with different dtypes than a fresh
    empty chain_from_json frame -- behaviorally inert, select_contract returns
    None on both; proven by the A16 skeptic's engine-parity run.)"""
    obs = pd.Timestamp(obs).normalize()
    path = path or snapshot_path(obs)
    try:
        with open(path) as f:
            payload = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None
    if not isinstance(payload, dict) or payload.get("obs") != str(obs.date()):
        return None
    raw = payload.get("chains")
    if not isinstance(raw, dict):
        return None
    out = {}
    try:
        for tk, rows in raw.items():
            # "corrupt -> None" must hold one level down too (skeptic F1): a
            # garbage-but-valid-JSON body otherwise either raises out of the
            # DataFrame constructor on every 17:00 retry tick, or -- worse --
            # builds a NaN-filled frame that loads "successfully" and is served
            # to the engine. Only save_chain_snapshot ever writes this file, so
            # every row must carry every engine column; anything else is rot.
            if not isinstance(rows, list) or not all(
                    isinstance(r, dict) and set(_CHAIN_COLS) <= set(r) for r in rows):
                return None
            df = pd.DataFrame(rows, columns=_CHAIN_COLS)
            df["date"] = pd.to_datetime(df["date"])
            df["expiry"] = pd.to_datetime(df["expiry"])
            # JSON round-trips a missing delta as None, which flips the column to
            # object dtype and makes select_contract's `.abs()` raise (same trap
            # documented in market_live.add_chain_rows).
            df["delta"] = pd.to_numeric(df["delta"], errors="coerce")
            # C1: same dtype-poison guard for the timestamp columns -- a None
            # would flip them to object and break any future numeric consumer
            df["quote_time"] = pd.to_numeric(df["quote_time"], errors="coerce")
            df["trade_time"] = pd.to_numeric(df["trade_time"], errors="coerce")
            out[tk] = df
    except (ValueError, TypeError, KeyError):
        return None
    return out


def snapshot_pulled_at(obs, path=None):
    """The snapshot's pull start time (ISO string) or None. C1: the field was
    written from day one and never read -- the 17:00 consumer can now say how
    old its book is."""
    obs = pd.Timestamp(obs).normalize()
    path = path or snapshot_path(obs)
    try:
        with open(path) as f:
            payload = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload.get("pulled_at")
