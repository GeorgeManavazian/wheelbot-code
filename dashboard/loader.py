"""Find and read leaderboard CSVs written by scripts/screen.py."""
from pathlib import Path

import pandas as pd


def list_leaderboards(results_dir="results") -> list[Path]:
    """All leaderboard CSVs, newest first (filenames embed the timestamp)."""
    return sorted(Path(results_dir).glob("leaderboard_*.csv"), reverse=True)


def load_leaderboard(path) -> pd.DataFrame:
    lb = pd.read_csv(path)
    lb["error"] = lb["error"].fillna("")
    return lb


def split_errors(lb: pd.DataFrame):
    """(ok_rows, error_rows) — crashed runs are shown, never dropped."""
    ok = lb[lb["error"] == ""].reset_index(drop=True)
    bad = lb[lb["error"] != ""].reset_index(drop=True)
    return ok, bad
