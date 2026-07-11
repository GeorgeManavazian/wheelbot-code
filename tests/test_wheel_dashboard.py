import os
import pytest
from streamlit.testing.v1 import AppTest

FIX = "fixtures/spy_wheel_cycle.parquet"

# NOTE: AppTest.from_function only captures the function's own source lines
# (per its docstring: "must include any necessary imports"), not the module's
# top-level imports. dashboard/views/wheel.py::render() relies on module-level
# imports (st, pd, WheelConfig, run_wheel, wheel_report, spy_buy_hold), so
# from_function fails with NameError before it ever reaches app logic. Adapted
# per the brief's fallback: drive the real multipage app via from_file() +
# switch_page(), which exercises the actual wired-up dashboard/app.py.
@pytest.mark.skipif(not os.path.exists(FIX), reason="wheel fixture not built")
def test_wheel_page_runs_and_renders_metrics():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    assert not at.exception
    at.switch_page("views/wheel.py").run(timeout=60)
    assert not at.exception
    at.button(key="run_wheel").click().run(timeout=90)
    assert not at.exception
    assert len(at.metric) >= 1        # recent-headline tiles rendered


def test_wheel_page_has_intraday_toggle():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.switch_page("views/wheel.py").run(timeout=60)
    assert not at.exception
    assert any(cb.key == "intraday_tp" for cb in at.checkbox)
