"""Render smoke tests: every dashboard page must execute without exceptions
against a real screening batch. Skipped when results/ is empty (fresh clone) —
run a screen first. These catch what unit tests can't: Styler/plotly/Streamlit
integration bugs that only fire when a page actually renders.
"""
from pathlib import Path

import pytest

from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.skipif(
    not list(Path("results").glob("leaderboard_*.csv")),
    reason="needs a real screening batch in results/")


def _view_app(view_name):
    import streamlit as st

    from dashboard import loader
    from dashboard.views import compare, leaderboard, plateau, run_detail

    views = {"leaderboard": leaderboard, "run_detail": run_detail,
             "plateau": plateau, "compare": compare}
    st.session_state["lb_path"] = str(loader.list_leaderboards()[0])
    views[st.session_state["view_name"]].render()


def test_app_shell_and_default_page_render():
    at = AppTest.from_file("dashboard/app.py", default_timeout=120)
    at.run()
    assert not at.exception, at.exception[0].message


@pytest.mark.parametrize("view", ["leaderboard", "run_detail", "plateau",
                                  "compare"])
def test_each_view_renders(view):
    at = AppTest.from_function(_view_app, args=(view,))
    at.session_state["view_name"] = view
    at.default_timeout = 120
    at.run()
    assert not at.exception, f"{view}: {at.exception[0].message}"
