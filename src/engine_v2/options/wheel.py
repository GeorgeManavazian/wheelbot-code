"""Pure-wheel EOD backtest engine. Builds on the options chain + primitives.
No rolling, no intraday, no metrics (sub-project 5). Isolated from the equity
engine and the gate."""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from .select import select_contract, option_mark

@dataclass
class WheelConfig:
    starting_capital: float = 100_000.0
    put_delta: float = 0.20
    call_delta: float = 0.20
    target_dte: int = 7
    take_profit_pct: float | None = 0.50
    cash_yield: float = 0.0
    ticker: str = "SPY"
    contract_multiplier: int = 100
    commission_per_contract: float = 0.65
    # defense variants (amendment 2026-07-12b, repaired 2026-07-13) — all
    # default-off so plain behavior is byte-identical.
    call_min_strike: str | None = None   # "basis": covered calls only at strike >= net basis
    roll_tested_puts: bool = False       # mid-life roll of tested puts (credit-only, capped)
    liquidate_assignment: bool = False   # take assignment, dump all shares at that day's spot, back to puts
    put_stop_mult: float | None = None   # buy the put back when EOD ask >= mult x credit received (puts only)

@dataclass
class Trade:
    date: pd.Timestamp
    action: str
    contract: object
    contracts: int
    price_per_contract: float
    cash_after: float
    campaign_id: int = 0

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
    days_flat: int = 0
    warnings: list = None
    days_shares_uncovered: int = 0

def run_wheel(chain: pd.DataFrame, cfg: WheelConfig, intraday=None) -> WheelResult:
    if cfg.liquidate_assignment and cfg.call_min_strike is not None:
        raise ValueError("liquidate_assignment never holds shares; call_min_strike "
                         "governs held shares — enable one, not both")
    dates = sorted(pd.to_datetime(chain["date"]).unique())
    und = underlying_series(chain)
    # index the chain by date ONCE so per-day strike lookups touch ~one day's rows
    # instead of scanning the whole (multi-million-row) frame every iteration.
    by_date = {pd.Timestamp(k): g for k, g in chain.groupby("date")}
    mult = cfg.contract_multiplier
    cash, shares, phase, short = cfg.starting_capital, 0, "PUT", None
    basis = None  # assigned put's strike while shares are held (defense variants)
    campaign, rolls_this_campaign, campaign_premium = 0, 0, 0.0
    warnings, days_shares_uncovered = [], 0
    trades, equity = [], {}
    prev_d, days_flat = None, 0

    for d in dates:
        d = pd.Timestamp(d)
        spot = float(und.get(d))
        day_chain = by_date.get(d)

        # 0) accrue yield on idle cash (cash only — shares/liability are not collateral)
        if prev_d is not None and cfg.cash_yield > 0:
            cash *= (1 + cfg.cash_yield / 365) ** (d - prev_d).days
        prev_d = d

        # 1) manage an existing short: take-profit, then expiry resolution
        closed_today = None  # contract closed via TP this day (block same-day churn into it)
        if short is not None:
            c = short["contract"]; n = short["contracts"]
            mark = option_mark(day_chain, d, c)
            tp_fired = False
            # >= 1.0 means hold to expiry (never take profit)
            if cfg.take_profit_pct is not None and cfg.take_profit_pct < 1.0 and d < c.expiry:
                thresh = (1 - cfg.take_profit_pct) * short["credit"]
                key = (pd.Timestamp(c.expiry), float(c.strike), c.right)
                if intraday is not None and key in intraday:
                    bars = intraday[key]
                    day = (bars[bars["timestamp"].dt.normalize() == d]
                           .sort_values("timestamp").reset_index(drop=True))
                    # decide on bar i, fill at bar i+1's close (no same-bar fills).
                    # A cross on the day's LAST bar has no next bar -> no intraday
                    # fill; the EOD ask check below decides instead.
                    for i in range(len(day) - 1):
                        if day.iloc[i]["close"] <= thresh:
                            fill = day.iloc[i + 1]
                            cost = fill["close"] * mult * n + cfg.commission_per_contract * n
                            cash -= cost; campaign_premium -= cost
                            trades.append(Trade(fill["timestamp"],
                                "CLOSE_PUT" if c.right == "P" else "CLOSE_CALL",
                                c, n, float(fill["close"]), cash, campaign))
                            short = None; closed_today = c; tp_fired = True
                            break
                if not tp_fired and short is not None and mark is not None and mark.ask <= thresh:
                    cost = buy_cost(mark, n, cfg)
                    cash -= cost; campaign_premium -= cost
                    trades.append(Trade(d, "CLOSE_PUT" if c.right == "P" else "CLOSE_CALL",
                                        c, n, mark.ask, cash, campaign))
                    short = None; closed_today = c
            # put-stop (after the TP check; puts only — the shares anatomy showed
            # calls are not the losing leg). EOD marks only; no intraday stop.
            if (short is not None and cfg.put_stop_mult is not None and c.right == "P"
                    and mark is not None
                    and mark.ask >= cfg.put_stop_mult * short["credit"]):
                cost = buy_cost(mark, n, cfg)
                cash -= cost; campaign_premium -= cost
                trades.append(Trade(d, "STOP_CLOSE", c, n, mark.ask, cash, campaign))
                short = None; closed_today = c
            if short is not None and d == c.expiry:
                if c.right == "P":
                    if spot < c.strike:
                        cash -= c.strike * mult * n; shares += mult * n; phase = "CALL"
                        basis = c.strike
                        trades.append(Trade(d, "ASSIGNED", c, n, c.strike, cash, campaign))
                        if cfg.liquidate_assignment:
                            # pure put-write: dump the shares at spot same day
                            cash += shares * spot
                            trades.append(Trade(d, "LIQUIDATE", c, n, spot, cash, campaign))
                            shares = 0; phase = "PUT"; basis = None
                    else:
                        trades.append(Trade(d, "PUT_EXPIRED", c, n, 0.0, cash, campaign))
                else:
                    if spot > c.strike:
                        cash += c.strike * mult * n; shares -= mult * n; phase = "PUT"
                        basis = None
                        trades.append(Trade(d, "CALLED_AWAY", c, n, c.strike, cash, campaign))
                    else:
                        trades.append(Trade(d, "CALL_EXPIRED", c, n, 0.0, cash, campaign))
                short = None

        # 2) open a new short if flat and eligible. Same-day re-entry after a
        # take-profit close IS allowed (redeploy freed capital), but never back
        # into the identical contract just closed — that would be pure spread churn.
        if short is None:
            if phase == "PUT":
                c = select_contract(day_chain, d, "P", cfg.put_delta, cfg.target_dte, cfg.ticker)
                mark = option_mark(day_chain, d, c) if c is not None else None
                if c is not None and c != closed_today and mark is not None:
                    n = int(cash // (c.strike * mult))
                    if n > 0:
                        campaign += 1
                        rolls_this_campaign, campaign_premium = 0, 0.0
                        proceeds = sell_proceeds(mark, n, cfg)
                        cash += proceeds; campaign_premium += proceeds
                        short = {"contract": c, "contracts": n, "credit": mark.bid, "last_mid": mark.mid}
                        trades.append(Trade(d, "SELL_PUT", c, n, mark.bid, cash, campaign))
            elif phase == "CALL" and shares >= mult:
                floor = basis if (cfg.call_min_strike == "basis" and basis is not None) else None
                c = select_contract(day_chain, d, "C", cfg.call_delta, cfg.target_dte,
                                    cfg.ticker, min_strike=floor)
                mark = option_mark(day_chain, d, c) if c is not None else None
                if c is not None and c != closed_today and mark is not None:
                    n = shares // mult
                    proceeds = sell_proceeds(mark, n, cfg)
                    cash += proceeds; campaign_premium += proceeds
                    short = {"contract": c, "contracts": n, "credit": mark.bid, "last_mid": mark.mid}
                    trades.append(Trade(d, "SELL_CALL", c, n, mark.bid, cash, campaign))

        # flat = in cash with nothing writable. Holding shares with no writable
        # call is NOT flat — it is exposed.
        if short is None and not (phase == "CALL" and shares >= mult):
            days_flat += 1
        if short is None and phase == "CALL" and shares >= mult:
            days_shares_uncovered += 1

        # 3) mark equity (short MTM at mid; carry last mid across gaps)
        liab = 0.0
        if short is not None:
            mk = option_mark(day_chain, d, short["contract"])
            if mk is not None:
                short["last_mid"] = mk.mid
            liab = short["last_mid"] * mult * short["contracts"]
        equity[d] = cash + shares * spot - liab

    residual_settled = False
    if short is not None:
        last = pd.Timestamp(dates[-1])
        mk = option_mark(by_date.get(last), last, short["contract"])
        mid = mk.mid if mk is not None else short["last_mid"]
        cash -= mid * mult * short["contracts"]
        residual_settled = True
    return WheelResult(pd.Series(equity), trades, cash, shares, residual_settled,
                       days_flat=days_flat, warnings=warnings,
                       days_shares_uncovered=days_shares_uncovered)
