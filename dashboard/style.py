"""Visual identity: family colors, semantic red/green, table shading, plotly defaults.

Color is never the only signal — every shaded cell still shows its number.
"""

POS = "#16A34A"    # gains / good
NEG = "#DC2626"    # losses / bad
GRID = "#E2E8F0"   # subtle chart gridlines
MUTED = "#64748B"  # secondary text, unknown families

# One color per strategy family, identical on every page (colorblind-safe pair).
FAMILY_COLORS = {
    "momentum_rotation": "#2563EB",  # blue
    "ts_trend": "#EA580C",           # orange
}

FORMATS = {"cagr": "{:.1%}", "max_dd": "{:.1%}", "exposure": "{:.0%}",
           "best_year": "{:.1%}", "worst_year": "{:.1%}", "top2_share": "{:.0%}",
           "sharpe": "{:.2f}", "turnover": "{:.1f}"}


def family_color(name: str) -> str:
    return FAMILY_COLORS.get(name, MUTED)


def _shade(rgb: str, invert: bool = False):
    """Column shader: normalizes values, returns rgba backgrounds.

    No matplotlib dependency (pandas' background_gradient needs it);
    alpha stays ≤0.5 so the numbers underneath remain readable.
    """
    def apply(col):
        vals = col.astype(float)
        lo, hi = vals.min(), vals.max()
        span = hi - lo

        def cell(v):
            if span == 0 or v != v:  # constant column or NaN
                return ""
            frac = (v - lo) / span
            if invert:
                frac = 1 - frac
            return f"background-color: rgba({rgb}, {0.08 + 0.42 * frac:.2f})"

        return [cell(v) for v in vals]
    return apply


def style_metrics(df):
    """Styler: green shading for strong sharpe/cagr, red for deep drawdowns."""
    fmt = {c: f for c, f in FORMATS.items() if c in df.columns}
    styler = df.style.format(fmt, na_rep="—")
    for col in ("sharpe", "cagr"):
        if col in df.columns:
            styler = styler.apply(_shade("22, 163, 74"), subset=[col])
    if "max_dd" in df.columns:
        # max_dd is negative; invert so the deepest loss gets the strongest red
        styler = styler.apply(_shade("220, 38, 38", invert=True), subset=["max_dd"])
    return styler


def apply_plotly_defaults(fig):
    fig.update_layout(template="simple_white",
                      margin=dict(l=10, r=10, t=48, b=10))
    fig.update_xaxes(gridcolor=GRID, showgrid=True)
    fig.update_yaxes(gridcolor=GRID, showgrid=True)
    return fig
