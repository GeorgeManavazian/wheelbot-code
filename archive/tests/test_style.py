"""Color honesty: absolute sharpe anchors + within-family shade separation."""
import pandas as pd

from dashboard.style import shade_family, style_metrics

LUCK = 0.79


def _css_for(df, col, row=0, **kw):
    """All CSS applied to one cell, as a single 'prop: value; ...' string."""
    ctx = style_metrics(df, **kw)._compute().ctx
    cell = ctx.get((row, df.columns.get_loc(col)), [])
    return "; ".join(f"{p}: {v}" for p, v in cell)


def test_below_luck_gets_no_green():
    df = pd.DataFrame({"sharpe": [0.68, 0.30]})
    css = _css_for(df, "sharpe", row=0, luck_sharpe=LUCK)
    assert "22, 163, 74" not in css          # no green
    assert "100, 116, 139" in css            # gray = noise


def test_above_luck_gets_green_scaled():
    df = pd.DataFrame({"sharpe": [0.9, 1.5]})
    css_ok = _css_for(df, "sharpe", row=0, luck_sharpe=LUCK)
    css_top = _css_for(df, "sharpe", row=1, luck_sharpe=LUCK)
    assert "22, 163, 74" in css_ok and "22, 163, 74" in css_top


def test_none_luck_falls_back_to_relative():
    df = pd.DataFrame({"sharpe": [0.1, 0.2]})
    assert "22, 163, 74" in _css_for(df, "sharpe", row=1)  # old behavior


def test_highlight_label_tints_row():
    df = pd.DataFrame({"sharpe": [0.5], "label": ["benchmark_spy"]})
    css = _css_for(df, "label", row=0, highlight_label="benchmark_spy")
    assert "#F1F5F9" in css


def test_shade_family_distinct_and_identity():
    base = "#EA580C"
    assert shade_family(base, 0, 1) == base
    shades = [shade_family(base, i, 3) for i in range(3)]
    assert len(set(shades)) == 3
