"""Single source of truth for where the live store lives.

The bot on the VPS writes the canonical store at the default `data/live`. The
Mac's dashboard reads a *clone* of the synced state repo instead, so it needs
the same layout rooted somewhere else -- hence the env override.

WHEELBOT_STATE_DIR is read at IMPORT time, so it must be set before importing
anything under live/ (scripts/dashboard.sh does this). Reading it once keeps
every consumer -- accounts, gaps, config, logs -- pointed at the same root; a
per-module default would let the dashboard show fresh trades beside a stale
gap ledger, which is exactly the kind of half-truth this project avoids."""
from __future__ import annotations
import os

DEFAULT_STATE_ROOT = "data/live"


def state_root() -> str:
    """Root of the live store. `data/live` unless WHEELBOT_STATE_DIR says else."""
    return os.environ.get("WHEELBOT_STATE_DIR") or DEFAULT_STATE_ROOT


def in_state(*parts: str) -> str:
    """Path to something inside the live store."""
    return os.path.join(state_root(), *parts)
