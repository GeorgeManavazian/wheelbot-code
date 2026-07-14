def test_regime_view_helpers_render_without_streamlit():
    # the view's pure helpers must work headless (streamlit only decorates)
    from dashboard.views.regime import current_state_lines, base_rate_display
    lines = current_state_lines("SPY")
    assert any("SPY" in l for l in lines)
    tbl = base_rate_display("SPY")
    assert "caveat" in tbl.attrs

def test_short_history_symbol_degrades_gracefully(tmp_path, monkeypatch):
    import pandas as pd
    from dashboard.views import regime as view
    short = pd.Series(range(100, 150),
                      index=pd.bdate_range("2026-01-01", periods=50), dtype=float)
    monkeypatch.setattr(view, "closes_for", lambda sym: short)
    lines = view.current_state_lines("NEWTKR")
    assert "not enough history" in lines[0]
