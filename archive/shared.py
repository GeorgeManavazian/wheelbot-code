"""Streamlit-side caching wrappers around the pure loader/recompute modules."""
import streamlit as st

from dashboard import benchmark, loader, recompute
from src.batch.runner import luck_sharpe
from src.engine.data import load_playground


@st.cache_resource
def playground():
    return load_playground()


@st.cache_data
def leaderboard_df(path_str: str):
    return loader.load_leaderboard(path_str)


@st.cache_data
def run_result(row: dict):
    """~1s first click per run, instant after (spec: recompute on demand)."""
    return recompute.recompute_run(row, playground())


def current_lb():
    return leaderboard_df(str(st.session_state["lb_path"]))


@st.cache_data
def spy_benchmark():
    """(equity, leaderboard-row dict) for SPY buy-hold; (None, None) if absent."""
    eq = benchmark.spy_equity(playground())
    if eq is None:
        return None, None
    return eq, benchmark.benchmark_row(eq)


def luck_threshold() -> float:
    """Same n_runs/years the leaderboard banner uses."""
    dates = playground()["date"]
    years = (dates.max() - dates.min()).days / 365.25
    return luck_sharpe(len(current_lb()), years)
