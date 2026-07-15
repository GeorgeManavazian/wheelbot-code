import os
import pytest
from streamlit.testing.v1 import AppTest
from streamlit.util import calc_md5

FIX = "fixtures/spy_wheel_cycle.parquet"

# NOTE: AppTest.from_function only captures the function's own source lines
# (per its docstring: "must include any necessary imports"), not the module's
# top-level imports. dashboard/views/wheel.py::render() relies on module-level
# imports (st, pd, WheelConfig, run_wheel, wheel_report, spy_buy_hold), so
# from_function fails with NameError before it ever reaches app logic. Adapted
# per the brief's fallback: drive the real multipage app via from_file() +
# switch_page(), which exercises the actual wired-up dashboard/app.py.
FIXTURE_TICKER = "SPY (2024 sample fixture)"


@pytest.mark.skipif(not os.path.exists(FIX), reason="wheel fixture not built")
def test_wheel_page_runs_and_renders_metrics():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    assert not at.exception
    at.switch_page("views/wheel.py").run(timeout=60)
    assert not at.exception
    # Pin to the committed fixture: real per-ticker chains (GDX, SPY, ...) now
    # populate the dropdown and sort first, and a full-history run is too slow
    # for a smoke test.
    at.selectbox(key="wheel_data").set_value(FIXTURE_TICKER).run(timeout=60)
    at.button(key="run_wheel").click().run(timeout=90)
    assert not at.exception
    assert len(at.metric) >= 4        # P&L / Sharpe / maxDD / vs SPY tiles


def test_wheel_page_matches_engine_api_contract():
    # 2026-07-12 contract: target_dte is the only DTE input (dte_min/dte_max
    # are gone), take-profit is a 1-100 slider, the DTE band is a read-only
    # caption derived from the target — never a widget.
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.switch_page("views/wheel.py").run(timeout=60)
    assert not at.exception
    number_keys = {n.key for n in at.number_input}
    assert "w_tdte" in number_keys
    assert "w_dmin" not in number_keys and "w_dmax" not in number_keys
    assert any(s.key == "w_tp" for s in at.slider)
    assert any("trades expiries" in c.value for c in at.caption)


def test_wheel_page_has_intraday_toggle():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.switch_page("views/wheel.py").run(timeout=60)
    assert not at.exception
    assert any(cb.key == "intraday_tp" for cb in at.checkbox)


def test_wheel_page_has_date_inputs():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.switch_page("views/wheel.py").run(timeout=60)
    assert not at.exception
    assert any(di.key == "wheel_start" for di in at.date_input)
    assert any(di.key == "wheel_end" for di in at.date_input)


def test_wheel_page_has_no_defense_selectbox():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.switch_page("views/wheel.py").run(timeout=60)
    assert not at.exception
    keys = {s.key for s in at.selectbox}
    assert "w_defense" not in keys


def test_history_page_renders():
    # NOTE: AppTest.switch_page() derives its target page hash from the
    # *filename* of the given path (page_icon_and_name), then matches it
    # against each StreamlitPage's hash of calc_md5(url_path). That only
    # lines up by coincidence for run.py/wheel.py (filename == url_path).
    # dashboard/app.py deliberately sets url_path="history" for the History
    # page (readable URL) while the view file is wheel_history.py (paired
    # with dashboard/wheel_history.py), so filename-based switch_page can't
    # find it. Set the internal page hash directly to the one st.navigation
    # actually computes (calc_md5(url_path)) so this drives the real,
    # wired-up multipage app rather than reimplementing it.
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at._page_hash = calc_md5("history")
    at.run(timeout=60)
    assert not at.exception
    assert any(b.key == "clear_history" for b in at.button)
