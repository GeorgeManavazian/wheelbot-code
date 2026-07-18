"""The paper-account matrix: 5 capital levels x 5 N values = 25 independent
live paper accounts, all running the same strategy on the same days so capital
AND N are tested in parallel (no sequential forward-testing). Each account has
its own store under data/live/accounts/<label>/."""
from __future__ import annotations
import os

CAPITALS = [5_000, 50_000, 100_000, 250_000, 500_000]
NS = [1, 2, 3, 4, 5]
ACCOUNTS_ROOT = "data/live/accounts"


def cap_label(capital: int) -> str:
    """5000 -> '5k', 250000 -> '250k'."""
    return f"{capital // 1000}k"


def account_label(capital: int, n: int) -> str:
    """5000, 3 -> '5k_N3'."""
    return f"{cap_label(capital)}_N{n}"


def account_dir(capital: int, n: int) -> str:
    return os.path.join(ACCOUNTS_ROOT, account_label(capital, n))


def account_paths(capital: int, n: int) -> dict:
    """state/trades/snapshots paths for one account."""
    d = account_dir(capital, n)
    return {"dir": d,
            "state": os.path.join(d, "state.json"),
            "trades": os.path.join(d, "trades.jsonl"),
            "snapshots": os.path.join(d, "snapshots.jsonl")}


def all_accounts():
    """[(capital, n), ...] -- the full 25-account grid."""
    return [(c, n) for c in CAPITALS for n in NS]
