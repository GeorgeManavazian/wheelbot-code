"""Persist wheel backtest runs to a gitignored CSV for the History tab."""
import os
import pandas as pd

RUNS_PATH = "data/wheel_runs.csv"

def log_run(record: dict) -> None:
    os.makedirs(os.path.dirname(RUNS_PATH), exist_ok=True)
    df = pd.DataFrame([record])
    header = not os.path.exists(RUNS_PATH)
    df.to_csv(RUNS_PATH, mode="a", header=header, index=False)

def load_runs() -> pd.DataFrame:
    if not os.path.exists(RUNS_PATH):
        return pd.DataFrame()
    return pd.read_csv(RUNS_PATH).iloc[::-1].reset_index(drop=True)  # newest first

def clear_runs() -> None:
    if os.path.exists(RUNS_PATH):
        os.remove(RUNS_PATH)
