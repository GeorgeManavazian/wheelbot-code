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

@dataclass
class WheelResult:
    equity: pd.Series
    trades: list
    final_cash: float
    final_shares: int
    residual_settled: bool = False

def run_wheel(chain: pd.DataFrame, cfg: WheelConfig) -> WheelResult:
    dates = sorted(pd.to_datetime(chain["date"]).unique())
    und = underlying_series(chain)
    mult = cfg.contract_multiplier
    cash, shares, phase, short = cfg.starting_capital, 0, "PUT", None
    trades, equity = [], {}

    for d in dates:
        d = pd.Timestamp(d)
        spot = float(und.get(d))

        # 1) manage an existing short: take-profit, then expiry resolution
        closed_today = None  # contract closed via TP this day (block same-day churn into it)
        if short is not None:
            c = short["contract"]; n = short["contracts"]
            mark = option_mark(chain, d, c)
            if (cfg.take_profit_pct is not None and mark is not None and d < c.expiry
                    and mark.ask <= (1 - cfg.take_profit_pct) * short["credit"]):
                cash -= buy_cost(mark, n, cfg)
                trades.append(Trade(d, "CLOSE_PUT" if c.right == "P" else "CLOSE_CALL",
                                    c, n, mark.ask, cash))
                short = None
                closed_today = c
            if short is not None and d == c.expiry:
                if c.right == "P":
                    if spot < c.strike:
                        cash -= c.strike * mult * n; shares += mult * n; phase = "CALL"
                        trades.append(Trade(d, "ASSIGNED", c, n, c.strike, cash))
                    else:
                        trades.append(Trade(d, "PUT_EXPIRED", c, n, 0.0, cash))
                else:
                    if spot > c.strike:
                        cash += c.strike * mult * n; shares -= mult * n; phase = "PUT"
                        trades.append(Trade(d, "CALLED_AWAY", c, n, c.strike, cash))
                    else:
                        trades.append(Trade(d, "CALL_EXPIRED", c, n, 0.0, cash))
                short = None

        # 2) open a new short if flat and eligible. Same-day re-entry after a
        # take-profit close IS allowed (redeploy freed capital), but never back
        # into the identical contract just closed — that would be pure spread churn.
        if short is None:
            if phase == "PUT":
                c = select_strike_by_delta(chain, d, "P", cfg.put_delta, cfg.dte_min, cfg.dte_max)
                mark = option_mark(chain, d, c) if c is not None else None
                if c is not None and c != closed_today and mark is not None:
                    n = int(cash // (c.strike * mult))
                    if n > 0:
                        cash += sell_proceeds(mark, n, cfg)
                        short = {"contract": c, "contracts": n, "credit": mark.bid, "last_mid": mark.mid}
                        trades.append(Trade(d, "SELL_PUT", c, n, mark.bid, cash))
            elif phase == "CALL" and shares >= mult:
                c = select_strike_by_delta(chain, d, "C", cfg.call_delta, cfg.dte_min, cfg.dte_max)
                mark = option_mark(chain, d, c) if c is not None else None
                if c is not None and c != closed_today and mark is not None:
                    n = shares // mult
                    cash += sell_proceeds(mark, n, cfg)
                    short = {"contract": c, "contracts": n, "credit": mark.bid, "last_mid": mark.mid}
                    trades.append(Trade(d, "SELL_CALL", c, n, mark.bid, cash))

        # 3) mark equity (short MTM at mid; carry last mid across gaps)
        liab = 0.0
        if short is not None:
            mk = option_mark(chain, d, short["contract"])
            if mk is not None:
                short["last_mid"] = mk.mid
            liab = short["last_mid"] * mult * short["contracts"]
        equity[d] = cash + shares * spot - liab

    residual_settled = False
    if short is not None:
        last = pd.Timestamp(dates[-1])
        mk = option_mark(chain, last, short["contract"])
        mid = mk.mid if mk is not None else short["last_mid"]
        cash -= mid * mult * short["contracts"]
        residual_settled = True
    return WheelResult(pd.Series(equity), trades, cash, shares, residual_settled)
