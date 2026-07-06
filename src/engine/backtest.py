"""Daily backtest loop.

Timeline per bar T (except the last):
  1. strategy sees closes up to and including T  (truncated copy — no future)
  2. non-None targets -> fill at T+1 OPEN with slippage
Equity is marked at each day's close. Same-bar fills are impossible:
the fill price row (T+1) is never inside the strategy's window (<= T).

NaN closes anywhere are a data bug -> hard error. The universe must be
complete from the first bar; nothing is silently dropped or interpolated.
"""
from dataclasses import dataclass

import pandas as pd

from src.engine.data import to_wide
from src.engine.fills import execute_rebalance


@dataclass
class BacktestConfig:
    initial_cash: float = 100_000.0
    slippage_bps: float = 5.0


@dataclass
class BacktestResult:
    equity: pd.Series
    holdings_value: pd.Series
    trades: pd.DataFrame


def run_backtest(long_df: pd.DataFrame, strategy, config: BacktestConfig = None) -> BacktestResult:
    config = config or BacktestConfig()
    closes = to_wide(long_df, "close")
    opens = to_wide(long_df, "open")

    if closes.iloc[0].isna().any():
        missing = closes.columns[closes.iloc[0].isna()].tolist()
        raise ValueError(
            f"universe must be complete from the first bar; NaN at start for {missing}. "
            "The expanding strategy window hides a column with any NaN in history."
        )

    positions: dict[str, int] = {}
    cash = config.initial_cash
    equity, holdings, all_trades = [], [], []
    pending_targets = None

    for i, date in enumerate(closes.index):
        # 1. fill yesterday's decision at today's open
        if pending_targets is not None:
            positions, cash, trades = execute_rebalance(
                positions, cash, pending_targets, opens.loc[date],
                config.slippage_bps)
            for tr in trades:
                all_trades.append({"date": date, **tr})
            pending_targets = None

        # 2. mark at close
        row = closes.loc[date]
        if row.isna().any():
            bad = closes.columns[row.isna()].tolist()
            raise ValueError(f"NaN close for {bad} on {date.date()}")
        pos_value = sum(sh * row[t] for t, sh in positions.items())
        equity.append(cash + pos_value)
        holdings.append(pos_value)

        # 3. decide (skip last bar — no next open to fill at)
        if i < len(closes.index) - 1:
            window = closes.iloc[: i + 1].copy()  # copy = mutation-proof
            targets = strategy.target_weights(window)
            if targets is not None:
                for t in targets:
                    if t not in closes.columns:
                        raise ValueError(f"strategy returned unknown ticker {t}")
                pending_targets = targets

    return BacktestResult(
        equity=pd.Series(equity, index=closes.index, name="equity"),
        holdings_value=pd.Series(holdings, index=closes.index, name="holdings"),
        trades=pd.DataFrame(all_trades,
                            columns=["date", "ticker", "side", "shares", "price"]),
    )
