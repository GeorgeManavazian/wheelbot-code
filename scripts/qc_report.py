"""Run all QC checks on data/raw/ and write data/manifests/qc_report.md.

Exits non-zero on any violation — freezing (next step) is blocked until clean.
"""
import sys
from pathlib import Path

import pandas as pd

from src.engine import qc
from src.engine.universe import TICKERS

RAW_DIR = Path("data/raw")
OUT = Path("data/manifests/qc_report.md")


def main():
    frames = {t: pd.read_parquet(RAW_DIR / f"{t}.parquet") for t in TICKERS}
    spy_unadj = pd.read_parquet(RAW_DIR / "SPY_unadjusted.parquet")

    violations = []
    for t, df in frames.items():
        violations += [f"{t}: {v}" for v in qc.check_prices(df)]
        violations += [f"{t}: {v}" for v in qc.check_calendar(df, frames["SPY"])]
    violations += [f"SPY: {v}" for v in qc.check_spy_adjustment(frames["SPY"], spy_unadj)]

    report = qc.inception_report(frames)
    late = report[report["first_date"] > pd.Timestamp("2010-01-05")]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# QC Report", "", "## Inception dates", "", report.to_markdown(), ""]
    if len(late):
        lines += ["## WARNING: tickers entering after 2010-01-05", "",
                  late.to_markdown(), ""]
    lines += ["## Violations", ""]
    lines += [f"- {v}" for v in violations] if violations else ["None."]
    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT}; {len(violations)} violations, {len(late)} late-inception tickers")
    sys.exit(1 if violations else 0)


if __name__ == "__main__":
    main()
