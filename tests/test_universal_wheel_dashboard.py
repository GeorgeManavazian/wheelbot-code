from streamlit.testing.v1 import AppTest


def test_universal_wheel_page_runs_and_summarizes():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    assert not at.exception
    at.switch_page("views/universal_wheel.py").run(timeout=60)
    assert not at.exception
    # narrow the window so the smoke run is fast, then run
    at.date_input(key="uw_start").set_value(__import__("datetime").date(2021, 1, 4)).run(timeout=60)
    at.date_input(key="uw_end").set_value(__import__("datetime").date(2021, 6, 30)).run(timeout=60)
    at.button(key="run_uw").click().run(timeout=180)
    assert not at.exception
    assert len(at.metric) >= 4     # P&L / finished-win / sold-today-win / avg%


def test_universal_wheel_no_reserved_tickers_on_page():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.switch_page("views/universal_wheel.py").run(timeout=60)
    assert not at.exception
    # the universe banner + any markdown must not name a reserved ticker
    blob = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    for reserved in ["XBI", "EEM", "EWZ", "TLT", "ARKK"]:
        assert reserved not in blob
