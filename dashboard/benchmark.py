"""SPY buy-and-hold benchmark computed from the frozen playground parquet.

TEMPORARY dashboard-side duplication (owner-approved): dies when the engine
grows real benchmark rows (already in the iteration queue).
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from src.engine.metrics import summarize

START_CASH = 100_000
LABEL = "benchmark_spy"
FRIENDLY = "S&P 500 buy & hold — benchmark"


def spy_equity(playground: pd.DataFrame) -> pd.Series | None:
    spy = playground[playground["ticker"] == "SPY"]
    if spy.empty:
        return None
    close = spy.set_index("date")["close"].sort_index()
    return close / close.iloc[0] * START_CASH


def benchmark_row(equity: pd.Series) -> dict:
    result = SimpleNamespace(
        equity=equity,
        trades=pd.DataFrame(columns=["shares", "price"]),
        holdings_value=equity,  # buy-hold: always fully invested
    )
    row = summarize(result)
    row.update(label=LABEL, name="benchmark", sample_flag="—",
               turnover=0.0, exposure=1.0)
    return row
