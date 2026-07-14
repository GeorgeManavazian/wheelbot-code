def test_regime_view_helpers_render_without_streamlit():
    # the view's pure helpers must work headless (streamlit only decorates)
    from dashboard.views.regime import current_state_lines, base_rate_display
    lines = current_state_lines("SPY")
    assert any("SPY" in l for l in lines)
    tbl = base_rate_display("SPY")
    assert "caveat" in tbl.attrs
