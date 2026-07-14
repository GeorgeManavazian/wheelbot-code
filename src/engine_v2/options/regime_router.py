"""Regime router (Regime Bot v1, spec 2026-07-14-regime-router-design): a
per-ticker strategy router. Uptrend -> hold shares; chop -> wheel + basis
floor; downtrend+stressed -> wheel; downtrend+quiet -> cash; unknown -> wheel.
Approach A borders: no forced exits on regime flips except trend shares on a
direct flip to downtrend; wheel-assigned shares are never regime-sold
(siege-exit falsification is binding precedent); covered calls open only in
wheel cells. Zero knobs, EOD fills. The WHEEL posture is a faithful transplant
of the solo plain+basis path — all-chop states must reproduce run_wheel
byte-identically (the anchor regression)."""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from .select import select_contract, option_mark
from .wheel import (Trade, WheelConfig, is_unpaid_decline, _state_before,
                    sell_proceeds, buy_cost)


@dataclass
class RouterResult:
    equity: pd.Series
    trades: list
    final_cash: float
    final_shares: int
    residual_settled: bool = False
    warnings: list = None
    route_log: list = None        # (date, trend, vol, posture) per day
    days_in_posture: dict = None  # {"CASH": n, "TREND": n, "WHEEL": n}
    days_shares_uncovered: int = 0
    whipsaw_pairs: int = 0        # SELL_SHARES <= 10 trading days after BUY_SHARES


def _cell(states: pd.DataFrame, d: pd.Timestamp):
    """(cell, trend, vol, unknown) from the strictly-prior-day state.
    Cells: TREND (uptrend), WHEEL (chop, downtrend+stressed, unknown),
    CASH (downtrend + calm/normal)."""
    trend, vol = _state_before(states, d)
    if (trend, vol) == ("unknown", "unknown"):
        return "WHEEL", trend, vol, True
    if trend == "uptrend":
        return "TREND", trend, vol, False
    if is_unpaid_decline(trend, vol):
        return "CASH", trend, vol, False
    return "WHEEL", trend, vol, False   # chop (any vol) or downtrend+stressed


def run_regime_router(chain: pd.DataFrame, cfg: WheelConfig,
                      regime_states: pd.DataFrame) -> RouterResult:
    if regime_states is None:
        raise ValueError("regime_states is required — a router without weather "
                         "is a bug, not a run")
    if cfg.roll_tested_puts or cfg.put_stop_mult is not None or \
            cfg.liquidate_assignment or cfg.any_regime_gate:
        raise ValueError("router v1 runs the plain+basis wheel only — "
                         "roll/stop/gates/liquidate are solo mechanics")

    dates = sorted(pd.to_datetime(chain["date"]).unique())
    und = chain.groupby("date")["underlying"].first()
    by_date = {pd.Timestamp(k): g for k, g in chain.groupby("date")}
    mult = cfg.contract_multiplier

    cash, campaign = cfg.starting_capital, 0
    # wheel sub-state, verbatim solo fields
    short, shares, phase, basis = None, 0, "PUT", None
    campaign_premium = 0.0
    # trend sub-state
    trend_shares, trend_buy_idx = 0, None   # idx into dates of the BUY fill
    whipsaw_pairs = 0
    warnings, route_log, trades, equity = [], [], [], {}
    days_in_posture = {"CASH": 0, "TREND": 0, "WHEEL": 0}
    prev_d, days_shares_uncovered = None, 0
    unknown_logged_days = set()

    for i, d in enumerate(dates):
        d = pd.Timestamp(d)
        spot = float(und.get(d))
        day_chain = by_date.get(d)
        if prev_d is not None and cfg.cash_yield > 0:
            cash *= (1 + cfg.cash_yield / 365) ** (d - prev_d).days
        prev_d = d
        cell, g_trend, g_vol, unknown = _cell(regime_states, d)

        # 1) manage an open short by SOLO rules (never consults the cell)
        closed_today = None
        if short is not None:
            c, n = short["contract"], short["contracts"]
            mark = option_mark(day_chain, d, c)
            if (cfg.take_profit_pct is not None and cfg.take_profit_pct < 1.0
                    and d < c.expiry and mark is not None
                    and mark.ask <= (1 - cfg.take_profit_pct) * short["credit"]):
                cost = buy_cost(mark, n, cfg)
                cash -= cost; campaign_premium -= cost
                trades.append(Trade(d, "CLOSE_PUT" if c.right == "P" else "CLOSE_CALL",
                                    c, n, mark.ask, cash, campaign))
                short = None; closed_today = c
            if short is not None and d >= c.expiry:
                settle_spot = spot
                if d > c.expiry:
                    warnings.append((d, "expiry_resolved_late", c))
                    pre = und[und.index <= c.expiry]
                    if len(pre):
                        settle_spot = float(pre.iloc[-1])
                if c.right == "P":
                    if settle_spot < c.strike:
                        cash -= c.strike * mult * n; shares += mult * n
                        phase = "CALL"; basis = c.strike
                        trades.append(Trade(d, "ASSIGNED", c, n, c.strike, cash, campaign))
                    else:
                        trades.append(Trade(d, "PUT_EXPIRED", c, n, 0.0, cash, campaign))
                else:
                    if settle_spot > c.strike:
                        cash += c.strike * mult * n; shares -= mult * n
                        phase = "PUT"; basis = None
                        trades.append(Trade(d, "CALLED_AWAY", c, n, c.strike, cash, campaign))
                    else:
                        trades.append(Trade(d, "CALL_EXPIRED", c, n, 0.0, cash, campaign))
                short = None

        # 2) transitions BEFORE entries (spec sequencing rule)
        if trend_shares and g_trend == "downtrend":
            # the single forced exit: the trend was the only thesis, and the
            # state says it is dead. Fill at EOD spot, no stock spread modeled.
            cash += trend_shares * spot
            trades.append(Trade(d, "SELL_SHARES", None, trend_shares // mult,
                                spot, cash, campaign))
            if trend_buy_idx is not None and (i - trend_buy_idx) <= 10:
                whipsaw_pairs += 1
            trend_shares, trend_buy_idx = 0, None
        elif trend_shares and g_trend == "chop":
            # hand trend shares to the wheel: rent them under the basis floor,
            # basis = purchase price (carried in `basis` at buy time).
            shares += trend_shares
            phase = "CALL"
            trend_shares, trend_buy_idx = 0, None

        # 3) entries / posture actions
        if short is None and shares == 0 and trend_shares == 0:
            # totally flat: route fresh
            if cell == "TREND":
                lots = int(cash // (spot * mult))
                if lots > 0:
                    campaign += 1
                    campaign_premium = 0.0
                    cost = lots * mult * spot
                    cash -= cost
                    trend_shares = lots * mult
                    trend_buy_idx = i
                    basis = spot   # purchase price; used if later handed to the wheel
                    trades.append(Trade(d, "BUY_SHARES", None, lots, spot, cash, campaign))
            elif cell == "WHEEL":
                if unknown and d not in unknown_logged_days:
                    warnings.append((d, "route_state_unknown", cfg.ticker))
                    unknown_logged_days.add(d)
                c = select_contract(day_chain, d, "P", cfg.put_delta,
                                    cfg.target_dte, cfg.ticker)
                mark = option_mark(day_chain, d, c) if c is not None else None
                if c is not None and c != closed_today and mark is not None:
                    n = int(cash // (c.strike * mult))
                    if n > 0:
                        campaign += 1
                        campaign_premium = 0.0
                        proceeds = sell_proceeds(mark, n, cfg)
                        cash += proceeds; campaign_premium += proceeds
                        short = {"contract": c, "contracts": n,
                                 "credit": mark.bid, "last_mid": mark.mid}
                        trades.append(Trade(d, "SELL_PUT", c, n, mark.bid, cash, campaign))
            # cell CASH: nothing — counted below
        elif (short is None and shares >= mult and phase == "CALL"
              and cell == "WHEEL" and day_chain is not None):
            # covered-call entry, solo rules; ONLY in wheel cells (spec rule 6).
            if unknown and d not in unknown_logged_days:
                warnings.append((d, "route_state_unknown", cfg.ticker))
                unknown_logged_days.add(d)
            floor = None
            if cfg.call_min_strike == "basis" and basis is not None:
                floor = basis - campaign_premium / shares
            c = select_contract(day_chain, d, "C", cfg.call_delta,
                                cfg.target_dte, cfg.ticker, min_strike=floor)
            mark = option_mark(day_chain, d, c) if c is not None else None
            if c is not None and c != closed_today and mark is not None:
                n = shares // mult
                proceeds = sell_proceeds(mark, n, cfg)
                cash += proceeds; campaign_premium += proceeds
                short = {"contract": c, "contracts": n,
                         "credit": mark.bid, "last_mid": mark.mid}
                trades.append(Trade(d, "SELL_CALL", c, n, mark.bid, cash, campaign))

        # 4) posture accounting + equity mark
        if trend_shares:
            posture = "TREND"
        elif short is not None or shares:
            posture = "WHEEL"
        else:
            posture = "CASH"
        days_in_posture[posture] += 1
        route_log.append((d, g_trend, g_vol, posture))
        if short is None and shares >= mult and phase == "CALL":
            days_shares_uncovered += 1
        liab = 0.0
        if short is not None:
            mk = option_mark(day_chain, d, short["contract"])
            if mk is not None:
                short["last_mid"] = mk.mid
            liab = short["last_mid"] * mult * short["contracts"]
        equity[d] = cash + (shares + trend_shares) * spot - liab

    residual_settled = False
    if short is not None:
        last = pd.Timestamp(dates[-1])
        mk = option_mark(by_date.get(last), last, short["contract"])
        mid = mk.mid if mk is not None else short["last_mid"]
        cash -= mid * mult * short["contracts"]
        residual_settled = True
    return RouterResult(pd.Series(equity), trades, cash, shares + trend_shares,
                        residual_settled, warnings=warnings, route_log=route_log,
                        days_in_posture=days_in_posture,
                        days_shares_uncovered=days_shares_uncovered,
                        whipsaw_pairs=whipsaw_pairs)
