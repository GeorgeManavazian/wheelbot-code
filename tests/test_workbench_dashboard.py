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

def test_run_page_shows_four_hero_tiles_including_vs_buy_hold():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.multiselect(key="tickers").set_value(["SPY", "TLT"]).run(timeout=60)
    at.button(key="run_backtest").click().run(timeout=120)
    assert not at.exception
    assert len(at.metric) >= 4
    assert any("Buy" in m.label for m in at.metric)

def test_headline_is_pnl_not_cagr():
    # Owner's rule #3 (wheel engine API contract): CAGR is not the headline
    # anywhere. It stays available in the full-history caption.
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.multiselect(key="tickers").set_value(["SPY", "TLT"]).run(timeout=60)
    at.button(key="run_backtest").click().run(timeout=120)
    assert not at.exception
    labels_shown = [m.label for m in at.metric]
    assert "P&L" in labels_shown
    assert "CAGR" not in labels_shown

def test_run_page_renders_plotly_charts_not_default_streamlit_charts():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.multiselect(key="tickers").set_value(["SPY", "TLT"]).run(timeout=60)
    at.button(key="run_backtest").click().run(timeout=120)
    assert not at.exception
    # equity, underwater, yearly sharpe, yearly return, monthly heatmap
    assert len(at.get("plotly_chart")) >= 5

def test_vs_buy_hold_tile_survives_spy_deselected_from_the_universe():
    # Deselecting SPY used to KeyError (caught by review before the 2026-07-10
    # merge). It no longer can: run.py derives bench_tickers from tickers_all,
    # not from the selection, so SPY buy & hold stays the benchmark whatever you
    # trade. That is the honest comparison — "would I have beaten just holding
    # SPY" is a fair question to ask of a TLT-only strategy too. So the tile must
    # show a REAL gap here, not "—".
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.multiselect(key="tickers").set_value(["TLT"]).run(timeout=60)
    at.button(key="run_backtest").click().run(timeout=120)
    assert not at.exception
    vs = [m for m in at.metric if "Buy" in m.label]
    assert vs, "vs Buy & Hold tile is missing"
    assert vs[0].value != "—", "SPY benchmark should still be available"
    assert vs[0].value.endswith("%")
    assert "SPY buy & hold" in vs[0].help

def test_cost_sweep_is_behind_a_button_and_does_not_run_automatically():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.multiselect(key="tickers").set_value(["SPY", "TLT"]).run(timeout=60)
    at.button(key="run_backtest").click().run(timeout=120)
    assert not at.exception
    assert any(b.key == "cost_sweep" for b in at.button)

def test_results_survive_pressing_the_cost_sweep_button():
    # Buttons don't nest in Streamlit: clicking cost_sweep reruns with
    # run_backtest False. The Result must persist in session_state, or the
    # whole page blanks and leaves a lone sweep table.
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.multiselect(key="tickers").set_value(["SPY", "TLT"]).run(timeout=60)
    at.button(key="run_backtest").click().run(timeout=120)
    at.button(key="cost_sweep").click().run(timeout=300)
    assert not at.exception
    assert len(at.metric) >= 4                      # hero tiles still there
    assert len(at.get("plotly_chart")) >= 5         # charts still there
    assert len(at.dataframe) >= 2                   # regime table + sweep table
