"""Human-readable names for strategies and runs.

Technical labels like momentum_rotation(lookback=63,...) remain the join key
everywhere in the data; these helpers only change what the owner SEES.
"""
import math

from dashboard.recompute import STRATEGIES

LOOKBACK_MONTHS = {21: "1mo", 42: "2mo", 63: "3mo", 125: "6mo",
                   189: "9mo", 252: "12mo"}


def _has(row: dict, key: str) -> bool:
    val = row.get(key)
    return val is not None and not (isinstance(val, float) and math.isnan(val))


def friendly_label(row: dict) -> str:
    """'Momentum Rotation · 3mo · top 5 · min score 20' from a leaderboard row."""
    name = row.get("name", "?")
    cls = STRATEGIES.get(name)
    base = getattr(cls, "display_name", "") or name.replace("_", " ").title()
    parts = [base]
    if _has(row, "lookback"):
        days = int(row["lookback"])
        parts.append(LOOKBACK_MONTHS.get(days, f"{days}d"))
    if _has(row, "top_n"):
        parts.append(f"top {int(row['top_n'])}")
    if _has(row, "min_score") and float(row["min_score"]) > 0:
        parts.append(f"min score {row['min_score']:g}")
    return " · ".join(parts)


def friendly_map(ok) -> dict:
    """label -> friendly name for every row; collisions get the raw label appended."""
    out = {row["label"]: friendly_label(row) for row in ok.to_dict("records")}
    counts = {}
    for f in out.values():
        counts[f] = counts.get(f, 0) + 1
    return {label: (f"{f} ({label})" if counts[f] > 1 else f)
            for label, f in out.items()}
