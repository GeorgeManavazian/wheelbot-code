import os
import pytest
from streamlit.testing.v1 import AppTest

FIX = "fixtures/spy_wheel_cycle.parquet"
FIXTURE_TICKER = "SPY (2024 sample fixture)"


@pytest.mark.skipif(not os.path.exists(FIX), reason="wheel fixture not built")
def test_chameleon_page_runs_and_renders():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    assert not at.exception
    at.switch_page("views/chameleon.py").run(timeout=60)
    assert not at.exception
    at.selectbox(key="cham_data").set_value(FIXTURE_TICKER).run(timeout=60)
    at.button(key="run_cham").click().run(timeout=120)
    assert not at.exception                     # must NOT raise (the days_flat crash)
    assert len(at.metric) >= 3                  # P&L / Sharpe / maxDD tiles


def test_chameleon_page_hides_unseen_tickers():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.switch_page("views/chameleon.py").run(timeout=60)
    assert not at.exception
    options = at.selectbox(key="cham_data").options
    for unseen in ["XBI", "EEM", "EWZ", "TLT", "ARKK", "QQQ"]:
        assert unseen not in options
