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


OVERLAY_COLS = ["x0", "x1", "y", "group", "open_ended", "hover"]


def _hover(r) -> str:
    bits = [f"{r['instrument']} @ {r['strike']:,.2f}", str(r["outcome"])]
    if pd.notna(r["credit"]):
        bits.append(f"credit ${r['credit']:,.2f}")
    if pd.notna(r["realized_pnl"]):
        bits.append(f"P&L ${r['realized_pnl']:,.2f}")
    if pd.notna(r["days_held"]):
        bits.append(f"{int(r['days_held'])}d held")
    bits.append(f"campaign {int(r['campaign_id'])}")
    return "<br>".join(bits)


def overlay_frame(blotter: pd.DataFrame, window_end) -> pd.DataFrame:
    """One drawable segment per position: a horizontal line at the strike,
    running from open to close.

    Every row has a strike, SHARES included — it is the assignment price, i.e.
    the cost basis (report.py:347). So y is uniform across instruments.

    A position is open-ended when it has no closing date. Two outcomes do that,
    "Open" and "Settled at mark", which is why this tests closed rather than
    matching outcome text.
    """
    window_end = pd.Timestamp(window_end)
    rows = []
    for _, r in blotter.iterrows():
        open_ended = pd.isna(r["closed"])
        rows.append({
            "x0": r["opened"],
            "x1": window_end if open_ended else r["closed"],
            "y": r["strike"],
            "group": outcome_group(r["realized_pnl"], r["outcome"]),
            "open_ended": open_ended,
            "hover": _hover(r),
        })
    return pd.DataFrame(rows, columns=OVERLAY_COLS)
