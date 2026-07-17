"""One Plotly template + the palette. Views must never hardcode a colour."""
import plotly.graph_objects as go
import plotly.io as pio

BG = "#0e1117"
PANEL = "#161b24"
BORDER = "#232a36"
TEXT = "#e6edf3"
MUTED = "#7d8798"
ACCENT = "#4f9dfd"
POSITIVE = "#3fb950"
NEGATIVE = "#f0836c"
WARNING = "#e3b341"

# Position outcome groups. trades.outcome_group() decides the group; this maps
# it to paint. Keys must stay in sync with that function's return values.
GROUP_COLORS = {
    "Lost money": NEGATIVE,
    "Assigned": WARNING,
    "Kept premium": POSITIVE,
    "Open": MUTED,
}

# Posture shading for the Chameleon chart (translucent bands behind candles).
POSTURE_COLORS = {
    "TREND": ACCENT,     # holding shares in an uptrend
    "WHEEL": WARNING,    # running the wheel
    "CASH": MUTED,       # sidelined
}

TEMPLATE_NAME = "quant_dark"


def register() -> None:
    """Register the Plotly template. Idempotent — Streamlit reruns call this often."""
    if TEMPLATE_NAME in pio.templates:
        return
    pio.templates[TEMPLATE_NAME] = go.layout.Template(
        layout=go.Layout(
            paper_bgcolor=BG,
            plot_bgcolor=BG,
            font=dict(color=TEXT, family="Inter, system-ui, sans-serif", size=12),
            xaxis=dict(gridcolor=BORDER, linecolor=BORDER, zerolinecolor=BORDER,
                       tickfont=dict(color=MUTED)),
            yaxis=dict(gridcolor=BORDER, linecolor=BORDER, zerolinecolor=BORDER,
                       tickfont=dict(color=MUTED)),
            colorway=[ACCENT, MUTED, WARNING, POSITIVE, NEGATIVE],
            margin=dict(l=10, r=10, t=30, b=10),
            hoverlabel=dict(bgcolor=PANEL, bordercolor=BORDER,
                            font=dict(color=TEXT)),
            legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=MUTED)),
        )
    )


def apply(fig: go.Figure) -> go.Figure:
    """Apply the template to a figure. Returns the same figure for chaining."""
    register()
    fig.update_layout(template=TEMPLATE_NAME, paper_bgcolor=BG, plot_bgcolor=BG)
    return fig
