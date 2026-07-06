"""Performance metrics + honesty stats. All from equity/trades — no magic."""
import numpy as np
import pandas as pd

TRADING_DAYS = 252


def cagr(equity: pd.Series) -> float:
    periods = len(equity) - 1
    if periods <= 0:
        return np.nan
    return (equity.iloc[-1] / equity.iloc[0]) ** (TRADING_DAYS / periods) - 1


def max_drawdown(equity: pd.Series) -> float:
    return (equity / equity.cummax() - 1).min()


def sharpe(equity: pd.Series) -> float:
    r = equity.pct_change().dropna()
    if len(r) == 0 or r.std() == 0:
        return np.nan
    return r.mean() / r.std() * np.sqrt(TRADING_DAYS)


def yearly_returns(equity: pd.Series) -> pd.Series:
    """Return per calendar year, chaining from previous year's last value."""
    last = equity.groupby(equity.index.year).last()
    first_value = equity.iloc[0]
    prev = last.shift(1)
    prev.iloc[0] = first_value
    return last / prev - 1


def annual_turnover(trades: pd.DataFrame, equity: pd.Series) -> float:
    if len(trades) == 0:
        return 0.0
    traded = (trades["shares"] * trades["price"]).sum()
    years = (len(equity) - 1) / TRADING_DAYS
    return traded / equity.mean() / years if years > 0 else np.nan


def avg_exposure(holdings_value: pd.Series, equity: pd.Series) -> float:
    return (holdings_value / equity).mean()


def summarize(result) -> dict:
    yr = yearly_returns(result.equity)
    n = len(result.trades)
    flag = "OK" if n >= 100 else ("LOW <100" if n >= 30 else "INSUFFICIENT <30")
    return {
        "cagr": cagr(result.equity),
        "max_dd": max_drawdown(result.equity),
        "sharpe": sharpe(result.equity),
        "n_trades": n,
        "turnover": annual_turnover(result.trades, result.equity),
        "exposure": avg_exposure(result.holdings_value, result.equity),
        "positive_years": int((yr > 0).sum()),
        "total_years": len(yr),
        "sample_flag": flag,
    }
