"""Shared unseen-ticker guard for the dashboard. Allow-list, not deny-list: the
CLI runner review (2026-07-14) rejected the deny-list because it fails open on
typos and new tickers. Only the burned/seen set is ever offered until the
pre-registered basket run reports (amendment 2026-07-13d)."""
from __future__ import annotations

import os

from src.engine_v2.options.data import available_tickers, chain_path

SEEN = ("SPY", "GDX", "SLV", "XOP")


def seen_sources(data_dir: str = "data/options",
                 include_fixture_name: str | None = None,
                 fixture_path: str | None = None) -> dict[str, str]:
    """Ticker -> EOD chain path, restricted to SEEN. Optionally append a named
    fixture when its file exists. Anything outside SEEN (unseen basket, fresh
    pulls) is excluded by construction."""
    out = {t: chain_path(t, data_dir) for t in available_tickers(data_dir) if t in SEEN}
    if include_fixture_name and fixture_path and os.path.exists(fixture_path):
        out[include_fixture_name] = fixture_path
    return out
