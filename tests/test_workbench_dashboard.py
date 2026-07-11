import os
import pytest
from streamlit.testing.v1 import AppTest
from src.engine_v2.data.source import INTRADAY_PATH

def test_run_page_renders_without_crash():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    assert not at.exception

def test_run_page_produces_a_result_on_run():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    # narrow scope so the smoke run is fast: 2 tickers, default (full) date range
    at.multiselect(key="tickers").set_value(["SPY", "TLT"]).run(timeout=60)
    at.button(key="run_backtest").click().run(timeout=120)
    assert not at.exception
    assert len(at.metric) >= 1  # recent-headline CAGR / Sharpe / maxDD tiles rendered

def test_data_source_selector_exists_and_daily_default():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    assert not at.exception
    assert at.selectbox(key="data_source").value.startswith("Daily")

@pytest.mark.skipif(not os.path.exists(INTRADAY_PATH),
                    reason="intraday cache not built on this machine")
def test_intraday_source_runs_in_dashboard():
    import datetime as _dt
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.selectbox(key="data_source").set_value("Intraday 1-min (SPY/QQQ)").run(timeout=60)
    # Narrow to ~5 trading days so the smoke run is fast — the full 5-year,
    # ~490k-bar minute backtest would take minutes inside AppTest. Task 2 already
    # proves run_simple's correctness on intraday bars; this only proves the UI wiring.
    sl = at.slider(key="date_range")
    lo_dt = sl.value[0]
    sl.set_value((lo_dt, lo_dt + _dt.timedelta(days=7))).run(timeout=60)
    at.multiselect(key="tickers").set_value(["SPY"]).run(timeout=60)
    at.button(key="run_backtest").click().run(timeout=120)
    assert not at.exception
    assert len(at.metric) >= 1
