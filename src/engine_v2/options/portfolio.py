"""Portfolio rotation (spec 2026-07-14-portfolio-rotation-design): one shared
cash pool over the seen universe, one campaign at a time, entries routed to the
eligible ticker with the richest premium (vol percentile desc, fixed tie order).
Zero knobs, EOD fills (v1), basis floor per spec. Solo-only mechanics
(roll/stop/gates/liquidate) are refused — this engine reuses the solo rules for
TP/expiry/covered-calls and adds ONLY the routing layer."""
from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd
from .select import select_contract, option_mark, liquidity_ok
from .fills import try_take_profit, tp_exit_feasible
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
    # A5: contracts the INTRADAY manager closed, date-stamped
    # [{"date": Timestamp, "contract": Contract}]. Seeds step_one_day's
    # closed_today so the 17:00 run cannot re-sell a contract bought back at
    # 10:00 -- the guard used to be rebuilt empty every step while
    # manage_intraday persisted nothing, and A6/A16 exist to increase
    # intraday closes. Stale (non-today) entries are pruned on append.
    intraday_closed: list = field(default_factory=list)


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


def close_short_fill(pos, dec, cash, trades):
    """Book a short close from a FillDecision -- THE one place that knows how
    a (possibly partial) buy-back mutates a position (A17). Decrements the
    live size by dec.filled_contracts; zero remaining normalizes `short` to
    None, which is what every downstream `short is None` test (compaction,
    covered-call gate, equity mark, settlement) keys on. Returns
    (cash, fully_closed). Both current fill modes are instant-and-whole
    (filled_contracts == contracts), so they decrement straight to zero --
    byte-identical to the pre-A17 behavior; the capacity exists for a fill
    model that is not. Shared with live/intraday.py -- one rule, two call
    sites, same as the A18 seam."""
    short = pos["short"]
    c = short["contract"]
    k = dec.filled_contracts
    # A17 skeptic F4: a filled decision carrying zero (or negative) contracts
    # would book a 0-lot ghost Trade, bleed dec.cost from cash, and leave the
    # leg open. Unreachable from the current seam (both modes fill whole);
    # refuse it loudly so a future fill model cannot emit it silently.
    if k <= 0:
        raise ValueError(f"filled decision with filled_contracts={k}")
    if k < short["contracts"] and "opened_contracts" not in short:
        # first partial on this leg: record the original size, or the stored
        # "6 remain" reads as 6-of-6 when it was 6-of-10 (skeptic F5)
        short["opened_contracts"] = short["contracts"]
    cash -= dec.cost
    pos["premium"] -= dec.cost
    # held legs load as Contract dataclasses but old raw dicts must not crash
    right = getattr(c, "right", None) if hasattr(c, "right") else c["right"]
    trades.append(Trade(dec.stamp, "CLOSE_PUT" if right == "P" else "CLOSE_CALL",
                        c, k, dec.price, cash, pos["campaign"]))
    short["contracts"] -= k
    fully = short["contracts"] <= 0
    if fully:
        pos["short"] = None
    return cash, fully


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
    # A5: seed with today's intraday closes -- same-day anti-churn must see
    # what the 10:00 manager did, not just what this step does
    day_norm = pd.Timestamp(d).normalize()
    for e in state.intraday_closed:
        if pd.Timestamp(e["date"]).normalize() == day_norm:
            closed_today.add(e["contract"])
    for pos in positions:
        tk = pos["ticker"]
        day_chain = market.chain(tk, d)
        spot = market.spot(tk, d, pos["last_spot"])
        pos["last_spot"] = spot
        short = pos["short"]
        if short is not None:
            c, n = short["contract"], short["contracts"]
            mark = option_mark(day_chain, d, c) if day_chain is not None else None
            dec = try_take_profit(mark=mark, credit=short["credit"], contracts=n,
                                  cfg=cfg, day=d, expiry=c.expiry)
            if dec.filled:
                cash, fully = close_short_fill(pos, dec, cash, trades)
                short = pos["short"]
                if fully:
                    closed_today.add(c)
                    # A5 skeptic F1: the step must leave a note for ITSELF
                    # too -- in the save-succeeded/snapshot-failed retry
                    # window the same day is re-stepped and this exact
                    # contract would be re-sold. Same date-stamped list the
                    # intraday manager writes; stale entries pruned.
                    state.intraday_closed = (
                        [e for e in state.intraday_closed
                         if pd.Timestamp(e["date"]).normalize() == day_norm]
                        + [{"date": day_norm, "contract": c}])
            if short is not None and d >= c.expiry:
                # re-read the size: a partial TP fill above shrank the leg, and
                # settling the stale pre-fill `n` would assign contracts that
                # were already bought back (A17/I3)
                n = short["contracts"]
                # Settlement reads the EXPIRY DAY's own close and nothing else.
                # It used to take `spot`, which degrades to the CARRIED
                # pos["last_spot"] when the close is missing — so on a data
                # outage a deep-ITM put was booked PUT_EXPIRED (worthless) with
                # no warning and no gap record. Measured on the real TMO leg:
                # identical market reality, two ledgers, $153,750 apart, and the
                # wrong one silent. zombie_check cannot catch it (it fires at 50%
                # of the universe; one ticker is 0.18%). settle_price() is not
                # the answer either — it returns the last close ON OR BEFORE the
                # expiry, i.e. a different day's price. Refuse instead: leave the
                # leg open and warn, so a later run with restored history settles
                # it correctly. (audit 2026-07-31)
                settle_spot = market.spot(tk, c.expiry, None)
                if settle_spot is None:
                    warnings.append((d, "expiry_unsettleable", c))
                    continue
                if d > c.expiry:
                    warnings.append((d, "expiry_resolved_late", c))
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
            if floor is not None and c is None:
                # A4: the basis floor sits above every strike the chain
                # carries -- the income half of the wheel cannot start and the
                # shares sit naked. Silent for months live (TMO class); never
                # silent again. (No warning when floor is None: a plain-wheel
                # run has no floor and no defect. No separate "no_mark" branch:
                # select_contract picks rows from the same frame option_mark
                # re-scans with the same key, and ingest guarantees float
                # bid/ask/mid -- a selected contract always marks; the skeptic
                # proved the branch dead.)
                warnings.append((d, "covered_call_unreachable", tk))
            feasible = mark is None or tp_exit_feasible(mark.bid, cfg)[0]
            if mark is not None and not feasible:
                # A3b (owner 2026-08-01): a covered call whose own TP exit is
                # unreachable at the tick or a guaranteed net loss is never
                # written -- the shares stay honestly naked for the day
                # (counted below, retried daily). Calls remain EXEMPT from
                # the A2 liquidity gate: refusing a call leaves shares naked,
                # so only arithmetic impossibility may refuse one.
                warnings.append((d, "call_gated_unclosable", tk))
            if c is not None and c not in closed_today and mark is not None \
                    and feasible:
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
        available = cash - committed
        pool = []
        for tk in market.universe:
            if tk in held_tickers:
                continue
            day_chain = market.chain(tk, d)
            if day_chain is None or not market.eligible(tk, d):
                continue
            row = market.regime_row(tk, d)
            if selector == "chop":
                if not is_good_renting_weather(row, cfg.chop_max_ma_spread,
                                               cfg.chop_max_fast_spread,
                                               cfg.chop_max_fast_fall):
                    continue
            else:
                if row is not None and is_unpaid_decline(row["trend"], row["vol"]):
                    continue
            c = select_contract(day_chain, d, "P", cfg.put_delta, cfg.target_dte, tk)
            mark = option_mark(day_chain, d, c) if c is not None else None
            if c is None or c in closed_today or mark is None:
                continue
            liq, why = liquidity_ok(day_chain, d, c, cfg)
            if not liq:
                # A2: veto, never substitute -- and never silently. A fully
                # gated day must not print like a quiet one (paper_step
                # surfaces these warnings in the run log). Deduped: the
                # n_slots while-loop revisits gated tickers every iteration
                # (skeptic F4).
                if (d, "entry_gated_illiquid", tk) not in warnings:
                    warnings.append((d, "entry_gated_illiquid", tk))
                continue
            if not tp_exit_feasible(mark.bid, cfg)[0]:
                # A3: this entry's own take-profit exit is unreachable at the
                # minimum tick or a guaranteed net loss -- unclosable by
                # construction (the WBD 25P $0.01-credit case).
                if (d, "entry_gated_unclosable", tk) not in warnings:
                    warnings.append((d, "entry_gated_unclosable", tk))
                continue
            if row is None:
                # deduped (A12 skeptic F4): the pool now includes unaffordable
                # tickers and rebuilds per while-iteration
                if (d, "route_state_unknown", tk) not in warnings:
                    warnings.append((d, "route_state_unknown", tk))
                pct = -1.0
            else:
                pct = float(row["vol_pctile"])
            pool.append((-pct, market.universe.index(tk), tk, c, mark))
        if not pool:
            break
        pool.sort()
        # A12: equal split FIRST (k == empty_slots is the old budget exactly,
        # so behavior is byte-identical whenever anything is affordable). When
        # NOTHING fits, re-split over fewer effective slots down to one --
        # $5k/N5 offered $1,000/slot, afforded nothing, and sat 82% idle
        # while a $3k contract was listed; the grid then measured which slots
        # could buy anything, not N. Concentration only when the alternative
        # is idleness. Fallback pick order = richest-ranked-first (owner
        # decision 2026-08-01; was least-concentration, skeptic F3).
        budget = available / empty_slots
        candidates = [(negpct, idx, tk_, c_, mk_,
                       int(budget // (c_.strike * mult)))
                      for (negpct, idx, tk_, c_, mk_) in pool
                      if int(budget // (c_.strike * mult)) > 0]
        if not candidates:
            # Fallback (owner 2026-08-01, skeptic F3): the BEST-RANKED name
            # that fits at ANY concentration wins, sized at the largest k
            # (least concentration) that affords it -- not the cheapest name
            # at the least concentration. Pool is already rank-sorted.
            for cand in pool:
                for k in range(empty_slots - 1, 0, -1):
                    n = int((available / k) // (cand[3].strike * mult))
                    if n > 0:
                        candidates = [(*cand, n)]
                        break
                if candidates:
                    break
        if not candidates:
            break
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
                pos["short"]["last_ask"] = mk.ask
            # Marked at the ASK (owner decision 2026-07-29, option B). A short
            # option is a liability dischargeable only by BUYING it back, and you
            # buy at the offer; the midpoint books half a spread the account can
            # never capture. Measured on the live paper run: +$31,752 reported at
            # mid became +$12,061 at exitable prices, and 7 of 25 accounts turned
            # out to be losing. This deliberately breaks the byte-identical
            # anchor -- every portfolio-engine number produced before this line
            # changed is non-comparable with one produced after.
            # `.get` fallback: legs recorded under the old scheme carry no
            # last_ask until their next successful mark.
            short_ = pos["short"]
            liab += short_.get("last_ask", short_["last_mid"]) * mult * short_["contracts"]
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
                        n_slots: int = 1,
                        universe: list | None = None) -> PortfolioResult:
    if selector not in ("vol_pctile", "chop"):
        raise ValueError(f"selector must be 'vol_pctile' or 'chop', got {selector!r}")
    if n_slots < 1:
        raise ValueError(f"n_slots must be >= 1, got {n_slots}")
    if cfg.roll_tested_puts or cfg.put_stop_mult is not None or \
            cfg.liquidate_assignment or cfg.any_regime_gate:
        raise ValueError("portfolio supports the plain+basis wheel only — "
                         "roll/stop/gates/liquidate are solo mechanics")
    if universe is None:
        # DEFAULT: the validated 9-ticker rotation. Sort by fixed tie order and
        # enforce the rotation/reserved/state allow-list.
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
    else:
        # EXPANDED-BACKTEST: the passed list is the ordering + allow-list. Filter
        # to tickers present in `chains` (preserving passed order); drop the
        # rotation/reserved restrictions but keep the regime_states requirement.
        universe = [t for t in universe if t in chains]
        for t in universe:
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
            # At the ASK, for the same reason the daily mark is (option B): this
            # finalizer BUYS the residual book back, and a buyer pays the offer.
            exit_px = mk.ask if mk is not None else \
                pos["short"].get("last_ask", pos["short"]["last_mid"])
            cash -= exit_px * mult * pos["short"]["contracts"]
            residual_settled = True
        if pos["shares"]:
            final_shares[pos["ticker"]] = final_shares.get(pos["ticker"], 0) + pos["shares"]
    return PortfolioResult(pd.Series(equity), trades, cash, final_shares,
                           residual_settled, days_flat=state.days_flat,
                           warnings=warnings,
                           days_shares_uncovered=state.days_shares_uncovered,
                           route_events=route_events,
                           n_campaigns_opened=state.campaign)
