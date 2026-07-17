"""Entry point:  .venv/bin/python -m streamlit run dashboard/app.py
Run from the repo root."""
import streamlit as st
from dashboard.views import chameleon, regime, run, universal_wheel, wheel_history

st.set_page_config(page_title="ETF Bot Workbench", layout="wide")
pg = st.navigation([
    st.Page(run.render, title="Run", url_path="run", default=True),
    st.Page(universal_wheel.render, title="Wheel", url_path="universal_wheel"),
    st.Page(chameleon.render, title="Chameleon", url_path="chameleon"),
    st.Page(regime.render, title="Regime", url_path="regime"),
    st.Page(wheel_history.render, title="History", url_path="history"),
])
pg.run()
