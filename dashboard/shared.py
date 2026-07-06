"""Streamlit-side caching wrappers around the pure loader/recompute modules."""
import streamlit as st

from dashboard import loader, recompute
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
