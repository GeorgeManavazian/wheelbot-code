"""Blotter -> plottable overlay. Pure pandas: no streamlit, no plotly. The
renderer is swappable; this module is what survives a swap."""
import pandas as pd


def outcome_group(realized_pnl, outcome) -> str:
    """Colour group for a position log row.

    Branch order is lifted verbatim from _style_blotter so the chart and the
    blotter can never disagree about the same trade: lost money beats assigned,
    assigned beats kept-premium, an unrealized row is still open.
    """
    if pd.notna(realized_pnl) and realized_pnl < 0:
        return "Lost money"
    if outcome == "Assigned":
        return "Assigned"
    if pd.notna(realized_pnl):
        return "Kept premium"
    return "Open"
