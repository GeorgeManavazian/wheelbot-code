import plotly.graph_objects as go
import plotly.io as pio
from dashboard import theme


def test_register_is_idempotent():
    theme.register()
    theme.register()  # a Streamlit rerun calls this again; must not raise
    assert theme.TEMPLATE_NAME in pio.templates


def test_apply_sets_the_template():
    theme.register()
    fig = theme.apply(go.Figure())
    assert fig.layout.template is not None
    assert fig.layout.paper_bgcolor == theme.BG


def test_palette_matches_the_spec():
    assert theme.BG == "#0e1117"
    assert theme.PANEL == "#161b24"
    assert theme.ACCENT == "#4f9dfd"
    assert theme.POSITIVE == "#3fb950"
    assert theme.NEGATIVE == "#f0836c"
