"""Single source of display names. Views must never hardcode a column header.

The 2026-07-06 friendly names were lost in the 2026-07-10 view rewrite because
they were scattered across view code. Keeping them here means a future rewrite
has exactly one file to notice.
"""
import pandas as pd

COLUMN_LABELS: dict[str, str] = {
    # Blotter
    "opened": "Opened",
    "closed": "Closed",
    "instrument": "Instrument",
    "strike": "Strike",
    "expiry": "Expiry",
    "qty": "Contracts",
    "credit": "Credit taken",
    "outcome": "Outcome",
    "cost_to_close": "Cost to close",
    "realized_pnl": "Realized P&L",
    "pct_of_credit": "Credit kept",
    "days_held": "Days held",
    # Regime table
    "dimension": "Dimension",
    "regime": "Regime",
    "sharpe": "Sharpe",
    "mean_ret": "Mean return",
    "bars": "Bars",
    # Benchmarks
    "SPY": "SPY buy & hold",
    "60_40": "60/40",
    "strategy": "Strategy",
    # Metrics
    "cagr": "CAGR",
    "max_drawdown": "Max drawdown",
    "total_return": "Total return",
    "spread_bps": "Spread (bps)",
}


def label(name: str) -> str:
    """Display name for a raw column. Unmapped names get a prettified fallback."""
    if name in COLUMN_LABELS:
        return COLUMN_LABELS[name]
    return str(name).replace("_", " ").capitalize()


def humanize(df: pd.DataFrame) -> pd.DataFrame:
    """Copy of `df` with display column names. Never mutates the argument."""
    return df.rename(columns={c: label(c) for c in df.columns})
