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


def shade_family(color: str, i: int, n: int) -> str:
    """i-th of n same-family shades: ±12% brightness steps around the base."""
    if n <= 1:
        return color
    r, g, b = (int(color[j:j + 2], 16) for j in (1, 3, 5))
    f = 1 + 0.12 * (i - (n - 1) / 2)
    def mix(c):
        return max(0, min(255, round(c * f)))
    return f"#{mix(r):02x}{mix(g):02x}{mix(b):02x}"


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


def _sharpe_shade_absolute(luck: float):
    """Below luck line: gray (noise). Luck→1.0: pale→mid green. >1: strong."""
    def apply(col):
        out = []
        for v in col.astype(float):
            if v != v:
                out.append("")
            elif v < luck:
                d = min((luck - v) / max(luck, 1e-9), 1.0)
                out.append(f"background-color: rgba(100, 116, 139, {0.06 + 0.18 * d:.2f})")
            else:
                frac = 1.0 if luck >= 1 else min((v - luck) / (1.0 - luck), 1.0)
                out.append(f"background-color: rgba(22, 163, 74, {0.15 + 0.35 * frac:.2f})")
        return out
    return apply


def style_metrics(df, luck_sharpe=None, highlight_label=None):
    """Styler: honest sharpe shading (absolute vs luck line when given),
    relative green for cagr, red for drawdowns, optional row tint."""
    fmt = {c: f for c, f in FORMATS.items() if c in df.columns}
    styler = df.style.format(fmt, na_rep="—")
    if highlight_label is not None and "label" in df.columns:
        def tint(row):
            hit = row["label"] == highlight_label
            return ["background-color: #F1F5F9" if hit else ""] * len(row)
        styler = styler.apply(tint, axis=1)   # first, so column shades win
    if "sharpe" in df.columns:
        shader = (_sharpe_shade_absolute(luck_sharpe) if luck_sharpe is not None
                  else _shade("22, 163, 74"))
        styler = styler.apply(shader, subset=["sharpe"])
    if "cagr" in df.columns:
        styler = styler.apply(_shade("22, 163, 74"), subset=["cagr"])
    if "max_dd" in df.columns:
        styler = styler.apply(_shade("220, 38, 38", invert=True), subset=["max_dd"])
    return styler


def apply_plotly_defaults(fig):
    fig.update_layout(template="simple_white",
                      margin=dict(l=10, r=10, t=48, b=10))
    fig.update_xaxes(gridcolor=GRID, showgrid=True)
    fig.update_yaxes(gridcolor=GRID, showgrid=True)
    return fig
