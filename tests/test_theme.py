import plotly.graph_objects as go
import plotly.io as pio
from dashboard import theme


def test_register_is_idempotent():
    theme.register()
    first_template = pio.templates[theme.TEMPLATE_NAME]
    first_template_id = id(first_template)

    theme.register()  # a Streamlit rerun calls this again; must not raise
    second_template = pio.templates[theme.TEMPLATE_NAME]
    second_template_id = id(second_template)

    # Proves the guard short-circuits; template is NOT rebuilt on re-registration
    assert first_template_id == second_template_id
    assert first_template is second_template


def test_apply_sets_the_template():
    theme.register()
    fig = theme.apply(go.Figure())
    assert fig.layout.template is not None
    assert fig.layout.paper_bgcolor == theme.BG


def test_palette_matches_the_spec():
    # All nine colour constants must match the spec exactly
    assert theme.BG == "#0e1117"
    assert theme.PANEL == "#161b24"
    assert theme.BORDER == "#232a36"
    assert theme.TEXT == "#e6edf3"
    assert theme.MUTED == "#7d8798"
    assert theme.ACCENT == "#4f9dfd"
    assert theme.POSITIVE == "#3fb950"
    assert theme.NEGATIVE == "#f0836c"
    assert theme.WARNING == "#e3b341"
