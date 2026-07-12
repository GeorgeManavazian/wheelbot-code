"""Entry point:  .venv/bin/python -m streamlit run dashboard/app.py
Run from the repo root."""
import streamlit as st
from dashboard.views import run, wheel, wheel_history

st.set_page_config(page_title="ETF Bot Workbench", layout="wide")
pg = st.navigation([
    st.Page(run.render, title="Run", url_path="run", default=True),
    st.Page(wheel.render, title="Wheel", url_path="wheel"),
    st.Page(wheel_history.render, title="History", url_path="history"),
])
pg.run()
