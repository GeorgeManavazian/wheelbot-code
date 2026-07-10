import pathlib
from streamlit.testing.v1 import AppTest


def test_verdict_panel_renders_pass(tmp_path):
    import json
    (tmp_path / "verdict.json").write_text(json.dumps({
        "pass": True, "watch": False, "shelf": False,
        "dsr": 0.99, "fwer": 0.01, "k_effective": 12,
        "regime_kill": False, "calmar_overall": 1.4, "notes": {},
    }))
    app_src = f"""
import json, streamlit as st
from dashboard.verdict_panel import render_verdict
v = json.loads(open({str(tmp_path / 'verdict.json')!r}).read())
render_verdict(v)
"""
    at = AppTest.from_string(app_src).run()
    md = " ".join(m.value for m in at.markdown)
    assert "PASS" in md
    assert "0.99" in md


def test_verdict_panel_renders_shelf():
    app_src = """
import streamlit as st
from dashboard.verdict_panel import render_verdict
render_verdict({"pass": False, "watch": False, "shelf": True,
                "dsr": 0.1, "fwer": 0.7, "k_effective": 20,
                "regime_kill": True, "calmar_overall": 0.3, "notes": {}})
"""
    at = AppTest.from_string(app_src).run()
    md = " ".join(m.value for m in at.markdown)
    assert "SHELF" in md
