"""Entry point:  .venv/bin/python -m streamlit run dashboard/app.py
Run from the repo root (loader looks for ./results)."""
import streamlit as st

from dashboard import loader
from dashboard.views import compare, leaderboard, plateau, run_detail

st.set_page_config(page_title="ETF Bot Research", layout="wide")

paths = loader.list_leaderboards()
if not paths:
    st.title("No batches yet")
    st.info("Run a screening first:\n\n"
            "```\n.venv/bin/python -m scripts.screen\n```\n"
            "then reload this page.")
    st.stop()

names = [p.name for p in paths]
choice = st.sidebar.selectbox("Batch", names)  # newest first
st.session_state["lb_path"] = paths[names.index(choice)]

pg = st.navigation([
    st.Page(leaderboard.render, title="Leaderboard", url_path="leaderboard",
            default=True),
    st.Page(run_detail.render, title="Run detail", url_path="run"),
    st.Page(plateau.render, title="Plateau", url_path="plateau"),
    st.Page(compare.render, title="Compare", url_path="compare"),
])
pg.run()
