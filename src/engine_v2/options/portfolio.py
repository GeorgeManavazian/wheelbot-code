"""Portfolio rotation (spec 2026-07-14-portfolio-rotation-design): one shared
cash pool over the seen universe, one campaign at a time, entries routed to the
eligible ticker with the richest premium (vol percentile desc, fixed tie order).
Zero knobs, EOD fills (v1), basis floor per spec. Solo-only mechanics
(roll/stop/gates/liquidate) are refused — this engine reuses the solo rules for
TP/expiry/covered-calls and adds ONLY the routing layer."""
from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd
from .select import select_contract, option_mark
from .wheel import (Trade, WheelConfig, is_unpaid_decline, sell_proceeds,
                    buy_cost, GATE_STALENESS_DAYS)
from ..regime.state import is_good_renting_weather
from .market import BatchMarket

ROTATION_TIE_ORDER = ("SPY", "GDX", "SLV", "XOP",
                      "AAPL", "AMZN", "NVDA", "META", "FB")
RESERVED_TICKERS = ("XBI", "EEM", "EWZ", "TLT", "ARKK")
# XOP's chain is split-broken before this date (unadjusted 1:4 reverse split
# 2020-03-31) — STATUS item; a ticker is ineligible before its clean start.
DEFAULT_CLEAN_START = {"XOP": pd.Timestamp("2020-07-01")}


@dataclass
class PortfolioResult:
    equity: pd.Series
    trades: list
    final_cash: float
    final_shares: dict
    residual_settled: bool = False
    days_flat: int = 0
    warnings: list = None
    days_shares_uncovered: int = 0
    route_events: list = None   # (date, ranked [(ticker, pctile)], chosen)
    n_campaigns_opened: int = 0


@dataclass
class PortfolioState:
    cash: float
    positions: list
    campaign: int = 0
    days_flat: int = 0
    days_shares_uncovered: int = 0
    prev_d: object = None


@dataclass
class StepResult:
    trades: list
    equity: float
    warnings: list
    route_events: list


def _row_before(states: pd.DataFrame, d: pd.Timestamp):
    """Full state row strictly before d, staleness-bounded (same information
    rule as the gates/autopsy). None -> unknown."""
    idx = states.index
    pos = idx.searchsorted(pd.Timestamp(d)) - 1
    if pos < 0 or (pd.Timestamp(d) - idx[pos]).days > GATE_STALENESS_DAYS:
        return None
    return states.iloc[pos]


def step_one_day(state, market, day, cfg, *, selector, n_slots) -> StepResult:
    """One trading day: manage held positions, drop finished campaigns, fill
    empty slots, mark equity. Mutates `state`; returns today's outputs. The
    SAME logic the batch backtest runs — reading through the Market seam."""
    d = pd.Timestamp(day)
    cash = state.cash
    positions = state.positions
    campaign = state.campaign
    days_flat = state.days_flat
    days_shares_uncovered = state.days_shares_uncovered
    prev_d = state.prev_d
    mult = cfg.contract_multiplier
    trades, warnings, route_events = [], [], []

    if prev_d is not None and cfg.cash_yield > 0:
        cash *= (1 + cfg.cash_yield / 365) ** (d - prev_d).days

    # 1) manage every held position (TP -> expiry -> covered call)
    closed_today = set()
    for pos in positions:
        tk = pos["ticker"]
        day_chain = market.chain(tk, d)
        spot = market.spot(tk, d, pos["last_spot"])
        pos["last_spot"] = spot
        short = pos["short"]
        if short is not None:
            c, n = short["contract"], short["contracts"]
            mark = option_mark(day_chain, d, c) if day_chain is not None else None
            if (cfg.take_profit_pct is not None and cfg.take_profit_pct < 1.0
                    and d < c.expiry and mark is not None
                    and mark.ask <= (1 - cfg.take_profit_pct) * short["credit"]):
                cost = buy_cost(mark, n, cfg)
                cash -= cost; pos["premium"] -= cost
                trades.append(Trade(d, "CLOSE_PUT" if c.right == "P" else "CLOSE_CALL",
                                    c, n, mark.ask, cash, pos["campaign"]))
                pos["short"] = None; short = None; closed_today.add(c)
            if short is not None and d >= c.expiry:
                settle_spot = spot
                if d > c.expiry:
                    warnings.append((d, "expiry_resolved_late", c))
                    sp = market.settle_price(tk, c.expiry)
                    if sp is not None:
                        settle_spot = sp
                if c.right == "P":
                    if settle_spot < c.strike:
                        cash -= c.strike * mult * n
                        pos["shares"] += mult * n; pos["phase"] = "CALL"
                        pos["basis"] = c.strike
                        trades.append(Trade(d, "ASSIGNED", c, n, c.strike, cash, pos["campaign"]))
                    else:
                        trades.append(Trade(d, "PUT_EXPIRED", c, n, 0.0, cash, pos["campaign"]))
                else:
                    if settle_spot > c.strike:
                        cash += c.strike * mult * n
                        pos["shares"] -= mult * n; pos["phase"] = "PUT"
                        pos["basis"] = None
                        trades.append(Trade(d, "CALLED_AWAY", c, n, c.strike, cash, pos["campaign"]))
                    else:
                        trades.append(Trade(d, "CALL_EXPIRED", c, n, 0.0, cash, pos["campaign"]))
                pos["short"] = None; short = None
        if (pos["short"] is None and pos["phase"] == "CALL"
                and pos["shares"] >= mult and day_chain is not None):
            floor = None
            if cfg.call_min_strike == "basis" and pos["basis"] is not None:
                floor = pos["basis"] - pos["premium"] / pos["shares"]
            c = select_contract(day_chain, d, "C", cfg.call_delta,
                                cfg.target_dte, tk, min_strike=floor)
            mark = option_mark(day_chain, d, c) if c is not None else None
            if c is not None and c not in closed_today and mark is not None:
                n = pos["shares"] // mult
                proceeds = sell_proceeds(mark, n, cfg)
                cash += proceeds; pos["premium"] += proceeds
                pos["short"] = {"contract": c, "contracts": n,
                                "credit": mark.bid, "last_mid": mark.mid}
                trades.append(Trade(d, "SELL_CALL", c, n, mark.bid, cash, pos["campaign"]))

    positions[:] = [p for p in positions
                    if not (p["short"] is None and p["shares"] == 0 and p["phase"] == "PUT")]

    # 2) routing entry: fill empty slots with the best good-to-rent tickers
    held_tickers = {p["ticker"] for p in positions}
    while len(positions) < n_slots:
        empty_slots = n_slots - len(positions)
        committed = sum(p["short"]["contract"].strike * mult * p["short"]["contracts"]
                        for p in positions
                        if p["short"] is not None and p["short"]["contract"].right == "P")
        budget = (cash - committed) / empty_slots
        candidates = []
        for tk in market.universe:
            if tk in held_tickers:
                continue
            day_chain = market.chain(tk, d)
            if day_chain is None or not market.eligible(tk, d):
                continue
            row = market.regime_row(tk, d)
            if selector == "chop":
                if not is_good_renting_weather(row):
                    continue
            else:
                if row is not None and is_unpaid_decline(row["trend"], row["vol"]):
                    continue
            c = select_contract(day_chain, d, "P", cfg.put_delta, cfg.target_dte, tk)
            mark = option_mark(day_chain, d, c) if c is not None else None
            if c is None or c in closed_today or mark is None:
                continue
            n = int(budget // (c.strike * mult))
            if n <= 0:
                continue
            if row is None:
                warnings.append((d, "route_state_unknown", tk))
                pct = -1.0
            else:
                pct = float(row["vol_pctile"])
            candidates.append((-pct, market.universe.index(tk), tk, c, mark, n))
        if not candidates:
            break
        candidates.sort()
        _, _, tk, c, mark, n = candidates[0]
        campaign += 1
        proceeds = sell_proceeds(mark, n, cfg)
        cash += proceeds
        positions.append({"ticker": tk, "shares": 0, "phase": "PUT", "basis": None,
                          "premium": proceeds, "campaign": campaign,
                          "last_spot": market.spot(tk, d, 0.0),
                          "short": {"contract": c, "contracts": n,
                                    "credit": mark.bid, "last_mid": mark.mid}})
        trades.append(Trade(d, "SELL_PUT", c, n, mark.bid, cash, campaign))
        route_events.append((d, [(t_[2], -t_[0]) for t_ in candidates], tk))
        held_tickers.add(tk)

    # 3) flat/uncovered accounting + equity mark
    if not positions:
        days_flat += 1
    for pos in positions:
        if pos["short"] is None and pos["phase"] == "CALL" and pos["shares"] >= mult:
            days_shares_uncovered += 1
    liab, shares_val = 0.0, 0.0
    for pos in positions:
        if pos["short"] is not None:
            day_chain = market.chain(pos["ticker"], d)
            mk = option_mark(day_chain, d, pos["short"]["contract"]) \
                if day_chain is not None else None
            if mk is not None:
                pos["short"]["last_mid"] = mk.mid
            liab += pos["short"]["last_mid"] * mult * pos["short"]["contracts"]
        shares_val += pos["shares"] * pos["last_spot"]
    equity_val = cash + shares_val - liab

    state.cash = cash
    state.positions = positions
    state.campaign = campaign
    state.days_flat = days_flat
    state.days_shares_uncovered = days_shares_uncovered
    state.prev_d = d
    return StepResult(trades, equity_val, warnings, route_events)


def run_portfolio_wheel(chains: dict, cfg: WheelConfig, regime_states: dict,
                        clean_start: dict | None = None,
                        selector: str = "vol_pctile",
                        n_slots: int = 1) -> PortfolioResult:
    if selector not in ("vol_pctile", "chop"):
        raise ValueError(f"selector must be 'vol_pctile' or 'chop', got {selector!r}")
    if n_slots < 1:
        raise ValueError(f"n_slots must be >= 1, got {n_slots}")
    if cfg.roll_tested_puts or cfg.put_stop_mult is not None or \
            cfg.liquidate_assignment or cfg.any_regime_gate:
        raise ValueError("portfolio supports the plain+basis wheel only — "
                         "roll/stop/gates/liquidate are solo mechanics")
    universe = sorted(chains, key=lambda t: ROTATION_TIE_ORDER.index(t)
                      if t in ROTATION_TIE_ORDER else len(ROTATION_TIE_ORDER))
    for t in universe:
        if t in RESERVED_TICKERS:
            raise ValueError(f"{t} is a reserved one-shot ticker — never a "
                             f"rotation universe member")
        if t not in ROTATION_TIE_ORDER:
            raise ValueError(f"{t} is not in the rotation universe "
                             f"{ROTATION_TIE_ORDER}")
        if t not in regime_states:
            raise ValueError(f"universe member {t} has no regime_states — "
                             f"routing without state is a bug, not a run")
    clean_start = {**DEFAULT_CLEAN_START, **(clean_start or {})}

    market = BatchMarket(chains, regime_states, clean_start, universe)
    dates = sorted({pd.Timestamp(d) for t in universe
                    for d in pd.to_datetime(chains[t]["date"]).unique()})
    mult = cfg.contract_multiplier

    state = PortfolioState(cash=cfg.starting_capital, positions=[])
    warnings, route_events, trades, equity = [], [], [], {}
    for d in dates:
        r = step_one_day(state, market, d, cfg, selector=selector, n_slots=n_slots)
        trades.extend(r.trades)
        warnings.extend(r.warnings)
        route_events.extend(r.route_events)
        equity[d] = r.equity

    # residual-settlement finalizer (batch-only; the live bot never runs this)
    cash = state.cash
    residual_settled = False
    final_shares = {}
    for pos in state.positions:
        if pos["short"] is not None:
            last = dates[-1]
            day_chain = market.chain(pos["ticker"], last)
            mk = option_mark(day_chain, last, pos["short"]["contract"]) \
                if day_chain is not None else None
            mid = mk.mid if mk is not None else pos["short"]["last_mid"]
            cash -= mid * mult * pos["short"]["contracts"]
            residual_settled = True
        if pos["shares"]:
            final_shares[pos["ticker"]] = final_shares.get(pos["ticker"], 0) + pos["shares"]
    return PortfolioResult(pd.Series(equity), trades, cash, final_shares,
                           residual_settled, days_flat=state.days_flat,
                           warnings=warnings,
                           days_shares_uncovered=state.days_shares_uncovered,
                           route_events=route_events,
                           n_campaigns_opened=state.campaign)
