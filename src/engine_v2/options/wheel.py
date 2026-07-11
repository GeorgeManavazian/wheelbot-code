"""Pure-wheel EOD backtest engine. Builds on the options chain + primitives.
No rolling, no intraday, no metrics (sub-project 5). Isolated from the equity
engine and the gate."""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from .select import select_strike_by_delta, option_mark

@dataclass
class WheelConfig:
    starting_capital: float = 100_000.0
    put_delta: float = 0.30
    call_delta: float = 0.30
    dte_min: int = 25
    dte_max: int = 45
    take_profit_pct: float | None = 0.50
    contract_multiplier: int = 100
    commission_per_contract: float = 0.65

@dataclass
class Trade:
    date: pd.Timestamp
    action: str
    contract: object
    contracts: int
    price_per_contract: float
    cash_after: float

def underlying_series(chain: pd.DataFrame) -> pd.Series:
    return chain.groupby("date")["underlying"].first()

def sell_proceeds(mark, contracts, cfg) -> float:
    return (mark.bid * cfg.contract_multiplier * contracts
            - cfg.commission_per_contract * contracts)

def buy_cost(mark, contracts, cfg) -> float:
    return (mark.ask * cfg.contract_multiplier * contracts
            + cfg.commission_per_contract * contracts)
