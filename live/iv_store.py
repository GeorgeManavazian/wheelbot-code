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

THE FILE STORES TWO DIFFERENT FACTS, and conflating them is a bug we already
shipped once: `records` is what the pull YIELDED, `pulled_tickers` is what the
pull REACHED. They differ constantly -- a ticker whose chain came back clean but
whose expiry ladder has nothing in the DTE band produces zero records and is
still completely done. Keying the resume on records re-pulled every such ticker
on every remaining in-window tick (true on ~83% of days for some names), which
is both 45x the intended API cost and a long retry.
"""
from __future__ import annotations

import json
import os

import pandas as pd

from live.paths import in_state


def iv_path(obs) -> str:
    return in_state("iv", f"{pd.Timestamp(obs).date()}.json")


def save_iv_day(obs, records: list, pulled_at: str, pulled_tickers=(),
                path=None) -> str:
    """Persist `records` and the roster of tickers actually reached, for `obs`.

    Atomic (tmp + rename): a reader must never see a half-written file from a
    pass that died mid-dump.

    `pulled_at` stamps the FILE (when this pass wrote it). Each record carries
    its own `pulled_at`, set by the runner at creation, so records carried
    forward from an earlier pass keep the time they were really measured."""
    obs = pd.Timestamp(obs).normalize()
    path = path or iv_path(obs)
    payload = {"obs": str(obs.date()), "pulled_at": pulled_at,
               "pulled_tickers": sorted(set(pulled_tickers)),
               "records": list(records)}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)
    return path


def _payload(obs, path=None):
    """The validated payload for exactly `obs`, or None. One reader for both
    accessors so a file can never be half-trusted (records honoured, roster
    silently defaulted -- which would re-pull the whole universe)."""
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
    if not isinstance(payload.get("records"), list):
        return None
    if not isinstance(payload.get("pulled_tickers"), list):
        return None
    return payload


def load_iv_day(obs, path=None):
    """The records for exactly `obs`, or None when no usable file exists.

    An EMPTY list is a real answer ("pulled fine, nothing selectable") and is
    deliberately distinguishable from None ("no usable file")."""
    payload = _payload(obs, path)
    return None if payload is None else payload["records"]


def tickers_pulled(obs, path=None):
    """The tickers whose chain pull SUCCEEDED for `obs`, or None when no usable
    file exists. This -- not the record list -- is what the resume is keyed on.

    An EMPTY list is again a real answer ("a pass ran and reached nobody"),
    distinguishable from None. Keyed on the ticker, not the grid cell: one API
    call produces every cell, so a ticker is either pulled or not."""
    payload = _payload(obs, path)
    if payload is None:
        return None
    return [t for t in payload["pulled_tickers"] if isinstance(t, str)]
