"""Data QC checks. Every check returns a list of violation strings; empty = pass.

House rule: hard-fail on violations, never silently patch data.
"""
import pandas as pd

PRICE_COLS = ["open", "high", "low", "close"]


def check_prices(df: pd.DataFrame) -> list[str]:
    """Zero, negative, or NaN prices."""
    out = []
    for date, row in df[PRICE_COLS].iterrows():
        if row.isna().any():
            out.append(f"{date.date()}: NaN price {row.to_dict()}")
        elif (row <= 0).any():
            out.append(f"{date.date()}: non-positive price {row.to_dict()}")
    return out


def check_calendar(df: pd.DataFrame, ref: pd.DataFrame) -> list[str]:
    """Missing bars vs a reference calendar (SPY trades every NYSE session).

    Only checks dates after the instrument's own first bar — an ETF that
    launched later is not 'missing' earlier bars (inception_report covers that).
    """
    expected = ref.index[ref.index >= df.index[0]]
    missing = expected.difference(df.index)
    return [f"missing bar {d.date()}" for d in missing]


def inception_report(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """First/last date per ticker so nothing silently enters mid-backtest."""
    rows = {
        t: {"first_date": df.index[0], "last_date": df.index[-1], "rows": len(df)}
        for t, df in frames.items()
    }
    return pd.DataFrame.from_dict(rows, orient="index").sort_values("first_date")


def check_spy_adjustment(adj: pd.DataFrame, unadj: pd.DataFrame) -> list[str]:
    """Adjusted and unadjusted SPY closes must diverge going back in time
    (dividends compound). If they're identical, auto_adjust silently failed."""
    joined = adj[["close"]].join(unadj[["close"]], rsuffix="_unadj").dropna()
    early = joined.iloc[: len(joined) // 2]
    if (early["close"] - early["close_unadj"]).abs().max() < 1e-6:
        return ["adjusted == unadjusted SPY closes: dividend adjustment missing"]
    return []
