from streamlit.testing.v1 import AppTest

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
