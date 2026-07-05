"""Frozen-data loaders. Playground loads freely; exam is gated and logged.

The exam gate is the multiple-testing defense: mass screening physically
cannot touch 2021+ data by accident.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def _load_verified(split: str, data_dir) -> pd.DataFrame:
    data_dir = Path(data_dir)
    path = data_dir / split / f"{split}.parquet"
    manifest = json.loads((data_dir / "manifests" / "manifest.json").read_text())
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != manifest[split]["sha256"]:
        raise RuntimeError(
            f"{split} checksum mismatch — file changed since freeze. "
            f"Expected {manifest[split]['sha256'][:12]}…, got {actual[:12]}…"
        )
    return pd.read_parquet(path)


def load_playground(data_dir="data") -> pd.DataFrame:
    return _load_verified("playground", data_dir)


def load_exam(confirm: bool = False, data_dir="data") -> pd.DataFrame:
    if not confirm:
        raise RuntimeError(
            "exam data is SEALED. Pass confirm=True only for a pre-registered "
            "exam run (pass bars written to a vault decision note first). "
            "This load will be permanently logged."
        )
    log = Path(data_dir) / "manifests" / "exam_runs.log"
    with open(log, "a") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} exam data loaded\n")
    return _load_verified("exam", data_dir)


def to_wide(long_df: pd.DataFrame, field: str) -> pd.DataFrame:
    wide = long_df.pivot(index="date", columns="ticker", values=field).sort_index()
    wide.columns.name = None
    return wide


def to_wide_closes(long_df: pd.DataFrame) -> pd.DataFrame:
    return to_wide(long_df, "close")
