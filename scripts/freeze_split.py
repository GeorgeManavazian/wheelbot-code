"""Freeze data/raw/ into playground (2010–2020) and exam (2021+) parquet
files with SHA256 manifest. Run ONCE after QC passes; re-running overwrites,
which invalidates prior results — only do that on an owner decision.
"""
import hashlib
import json
from pathlib import Path

import pandas as pd

from src.engine.universe import TICKERS

RAW_DIR = Path("data/raw")
SPLIT_END = "2020-12-31"  # playground <= this < exam


def main():
    frames = []
    for t in TICKERS:
        df = pd.read_parquet(RAW_DIR / f"{t}.parquet").reset_index()
        df["ticker"] = t
        frames.append(df)
    long_df = pd.concat(frames, ignore_index=True)
    long_df["date"] = pd.to_datetime(long_df["date"])

    cut = pd.Timestamp(SPLIT_END)
    splits = {"playground": long_df[long_df["date"] <= cut],
              "exam": long_df[long_df["date"] > cut]}

    manifest = {}
    for name, df in splits.items():
        out = Path(f"data/{name}/{name}.parquet")
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out, index=False)
        manifest[name] = {
            "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
            "rows": len(df),
            "start": str(df["date"].min().date()),
            "end": str(df["date"].max().date()),
            "tickers": sorted(df["ticker"].unique().tolist()),
        }
        print(f"{name}: {len(df)} rows {manifest[name]['start']} -> {manifest[name]['end']}")

    Path("data/manifests").mkdir(parents=True, exist_ok=True)
    Path("data/manifests/manifest.json").write_text(json.dumps(manifest, indent=2))
    print("manifest written")


if __name__ == "__main__":
    main()
