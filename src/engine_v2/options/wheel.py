"""Pure-wheel EOD backtest engine. Builds on the options chain + primitives.
No rolling, no intraday, no metrics (sub-project 5). Isolated from the equity
engine and the gate."""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from .select import select_contract, select_roll_contract, option_mark

MAX_ROLLS_PER_CAMPAIGN = 2   # then the normal expiry path (assignment) applies

GATE_STALENESS_DAYS = 14   # mirrors regime.autopsy.MAX_STALENESS_DAYS (kept in
                           # sync by hand: no shared module, to preserve the
                           # options/ -> regime/ import direction)

def is_unpaid_decline(trend: str, vol: str) -> bool:
    """Falling without panic premium — the one cell the gates act on
    (pre-registered, spec 2026-07-14). Never extended to a per-cell table."""
    return trend == "downtrend" and vol in ("calm", "normal")

def _state_before(states: pd.DataFrame, d: pd.Timestamp) -> tuple:
    """(trend, vol) from the last state row STRICTLY before d (a decision on
    day d cannot know day d's close — same rule as fills and autopsy tagging),
    bounded by GATE_STALENESS_DAYS. Missing/stale -> ("unknown","unknown"):
    gates never act on missing information."""
    idx = states.index
    pos = idx.searchsorted(pd.Timestamp(d)) - 1
    if pos < 0 or (pd.Timestamp(d) - idx[pos]).days > GATE_STALENESS_DAYS:
        return "unknown", "unknown"
    row = states.iloc[pos]
    return row["trend"], row["vol"]

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
    # regime gates (macro phase 2, spec 2026-07-14) — all default-off so the
    # plain path is byte-identical. Ticker state, strictly-prior-day.
    regime_entry_gate: bool = False   # no new campaign opens in unpaid decline
    regime_roll_gate: bool = False    # mid-life roll denied in unpaid decline
    regime_stop_gate: bool = False    # put stop suppressed while vol == "stressed"
    conviction_trim: bool = False     # half-size trend HOLD entries in stressed vol (router only)
    chop_max_ma_spread: float | None = None    # chop scanner: reject if |50d/200d-1| > this (default off)
    chop_max_fast_spread: float | None = None  # chop scanner: reject if |9d/20d-1| > this (default off)

    @property
    def any_regime_gate(self) -> bool:
        return self.regime_entry_gate or self.regime_roll_gate or self.regime_stop_gate

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
    gate_events: list = None      # (date, kind, contract|None) — regime-gate actions
    days_entry_gated: int = 0     # days the entry gate was the proximate blocker

def run_wheel(chain: pd.DataFrame, cfg: WheelConfig, intraday=None,
              regime_states: pd.DataFrame | None = None) -> WheelResult:
    if cfg.liquidate_assignment and cfg.call_min_strike is not None:
        raise ValueError("liquidate_assignment never holds shares; call_min_strike "
                         "governs held shares — enable one, not both")
    any_gate = cfg.any_regime_gate
    if any_gate and regime_states is None:
        raise ValueError("a regime gate is on but no regime_states was passed — "
                         "a gate with no state is a bug, not a run")
    if cfg.regime_stop_gate and cfg.put_stop_mult is None:
        raise ValueError("regime_stop_gate gates the put stop; put_stop_mult is "
                         "None so the arm would be a silent no-op — refuse it")
    if cfg.conviction_trim:
        raise ValueError("conviction_trim is a router-only mechanic (it sizes the "
                         "TREND hold); the solo wheel has no trend posture — set it "
                         "on run_regime_router, not run_wheel")
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
    gate_events, days_entry_gated = [], 0
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

        # regime state for today's gate checks: strictly-prior-day, staleness-
        # bounded. Computed once per day; ("unknown","unknown") never gates.
        g_trend = g_vol = None
        if any_gate:
            g_trend, g_vol = _state_before(regime_states, d)

        # 1) manage an existing short: take-profit, roll, stop, then expiry
        closed_today = None  # contract closed via TP this day (block same-day churn into it)
        no_entry_today = False  # set by STOP_CLOSE: stop means flat until tomorrow
        rolled_today = False    # a roll consumes the day's stop check (new leg re-evaluates tomorrow)
        mark_warned_today = False  # one missing-mark warning per day, not one per check
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
                    # close > 0 only: hourly bars are trade prints, and hours
                    # with no trade arrive as close=0 — not a price. Treating a
                    # 0 as a price lets any losing put "TP" at a phantom fill
                    # (XOP 2020: +2,582% fantasy). Trigger and fill both use
                    # valid prints only; "next bar" means next VALID bar.
                    day = (bars[(bars["timestamp"].dt.normalize() == d)
                                & (bars["close"] > 0)]
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
            # mid-life roll of a tested put (repair spec 2026-07-13): fires while
            # extrinsic is alive, only ever for a net credit, at most
            # MAX_ROLLS_PER_CAMPAIGN times per campaign. Destination re-uses the
            # entry config (delta, target DTE) — nothing is re-tuned.
            if (short is not None and cfg.roll_tested_puts and c.right == "P"
                    and d < c.expiry and spot <= c.strike
                    and rolls_this_campaign < MAX_ROLLS_PER_CAMPAIGN):
                if mark is None:
                    warnings.append((d, "roll_check_no_mark", c))
                    mark_warned_today = True
                else:
                    # destination: SAME strike, out in time — expiry strictly
                    # beyond the held leg, nearest to held_expiry + target_dte
                    # (amendment 2026-07-13b: same-tenor and down-and-out
                    # destinations never clear the credit-only bar on real
                    # chains; only the same-strike out-roll can self-fund).
                    new_c = select_roll_contract(day_chain, d, "P", c.strike,
                                                 c.expiry, cfg.target_dte, cfg.ticker)
                    new_mark = option_mark(day_chain, d, new_c) if new_c is not None else None
                    cost = buy_cost(mark, n, cfg)
                    proceeds = (sell_proceeds(new_mark, n, cfg)
                                if new_mark is not None else None)
                    # credit-only: guard and fill use the SAME numbers, so a
                    # future fill-model change cannot let debit rolls through.
                    # roll gate: no extension into an unpaid decline. Consulted
                    # only at the would-execute moment — after the destination
                    # and credit checks — so a logged denial means a roll that
                    # WOULD have executed (same would-act rule as the stop
                    # gate). Denial re-evaluates tomorrow and must NOT consume
                    # the stop check (only an EXECUTED roll does). Unknown
                    # state allows and warns.
                    roll_gated_today = False
                    if proceeds is not None and proceeds >= cost and cfg.regime_roll_gate:
                        if (g_trend, g_vol) == ("unknown", "unknown"):
                            warnings.append((d, "gate_state_unknown", "roll"))
                        elif is_unpaid_decline(g_trend, g_vol):
                            gate_events.append((d, "roll_denied_by_gate", c))
                            roll_gated_today = True
                    if proceeds is not None and proceeds >= cost and not roll_gated_today:
                        cash -= cost; campaign_premium -= cost
                        trades.append(Trade(d, "ROLL_CLOSE", c, n, mark.ask, cash, campaign))
                        cash += proceeds; campaign_premium += proceeds
                        short = {"contract": new_c, "contracts": n,
                                 "credit": new_mark.bid, "last_mid": new_mark.mid}
                        trades.append(Trade(d, "ROLL_OPEN", new_c, n, new_mark.bid, cash, campaign))
                        rolls_this_campaign += 1
                        c, mark = new_c, new_mark
                        rolled_today = True
            # put-stop (after the TP and roll checks; puts only — the shares
            # anatomy showed calls are not the losing leg). EOD marks only.
            # Never on expiry day (assignment settles at intrinsic; buying back
            # pays the spread on top). Firing means FLAT: no re-entry until
            # tomorrow.
            if (short is not None and cfg.put_stop_mult is not None and c.right == "P"
                    and d < c.expiry and not rolled_today):
                if mark is None:
                    if not mark_warned_today:   # roll check may already have logged it
                        warnings.append((d, "stop_check_no_mark", c))
                elif mark.ask >= cfg.put_stop_mult * short["credit"]:
                    # stop gate: never dump into panic — a stop that WOULD fire
                    # is suppressed while vol is stressed (logged, would-act
                    # moments only) and re-arms on the first non-stressed day.
                    if cfg.regime_stop_gate and g_vol == "stressed":
                        gate_events.append((d, "stop_suppressed_by_gate", c))
                    else:
                        if cfg.regime_stop_gate and (g_trend, g_vol) == ("unknown", "unknown"):
                            warnings.append((d, "gate_state_unknown", "stop"))
                        cost = buy_cost(mark, n, cfg)
                        cash -= cost; campaign_premium -= cost
                        trades.append(Trade(d, "STOP_CLOSE", c, n, mark.ask, cash, campaign))
                        short = None; closed_today = c; no_entry_today = True
            # >= not ==: if the expiry date itself is absent from the chain
            # (data gap — SPY has two such days), the position must still
            # resolve on the first trading day at/after expiry, else it becomes
            # an unmanageable zombie that freezes the engine for the rest of
            # the backtest. Late resolution is logged.
            if short is not None and d >= c.expiry:
                # moneyness is decided at the last close AT/BEFORE expiry —
                # past data, no look-ahead. Deciding with the post-gap spot
                # would book phantom assignments/exercises from price moves
                # that happened after the option was already dead.
                settle_spot = spot
                if d > c.expiry:
                    warnings.append((d, "expiry_resolved_late", c))
                    pre = und[und.index <= c.expiry]
                    if len(pre):
                        settle_spot = float(pre.iloc[-1])
                if c.right == "P":
                    if settle_spot < c.strike:
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
                    if settle_spot > c.strike:
                        cash += c.strike * mult * n; shares -= mult * n; phase = "PUT"
                        basis = None
                        trades.append(Trade(d, "CALLED_AWAY", c, n, c.strike, cash, campaign))
                    else:
                        trades.append(Trade(d, "CALL_EXPIRED", c, n, 0.0, cash, campaign))
                short = None

        # 2) open a new short if flat and eligible. Same-day re-entry after a
        # take-profit close IS allowed (redeploy freed capital), but never back
        # into the identical contract just closed — that would be pure spread churn.
        if short is None and not no_entry_today:
            if phase == "PUT":
                c = select_contract(day_chain, d, "P", cfg.put_delta, cfg.target_dte, cfg.ticker)
                mark = option_mark(day_chain, d, c) if c is not None else None
                if c is not None and c != closed_today and mark is not None:
                    n = int(cash // (c.strike * mult))
                    if n > 0:
                        # entry gate: a NEW campaign never opens into an unpaid
                        # decline (the call phase below continues an old
                        # campaign — not gated). Consulted only HERE, at the
                        # would-open moment — after selection/mark/sizing — so
                        # days_entry_gated counts exactly the days the gate was
                        # the proximate blocker, not days the baseline could
                        # not have entered anyway. Unknown state allows and
                        # warns (never gate on missing information).
                        entry_gated_today = False
                        if cfg.regime_entry_gate:
                            if (g_trend, g_vol) == ("unknown", "unknown"):
                                warnings.append((d, "gate_state_unknown", "entry"))
                            elif is_unpaid_decline(g_trend, g_vol):
                                days_entry_gated += 1
                                gate_events.append((d, "entry_gated", None))
                                entry_gated_today = True
                        if not entry_gated_today:
                            campaign += 1
                            rolls_this_campaign, campaign_premium = 0, 0.0
                            proceeds = sell_proceeds(mark, n, cfg)
                            cash += proceeds; campaign_premium += proceeds
                            short = {"contract": c, "contracts": n, "credit": mark.bid, "last_mid": mark.mid}
                            trades.append(Trade(d, "SELL_PUT", c, n, mark.bid, cash, campaign))
            elif phase == "CALL" and shares >= mult:
                floor = None
                if cfg.call_min_strike == "basis" and basis is not None:
                    # net basis: assignment strike minus premium already banked
                    # this campaign — the anchor ratchets DOWN as rent comes in.
                    floor = basis - campaign_premium / shares
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
                       days_shares_uncovered=days_shares_uncovered,
                       gate_events=gate_events, days_entry_gated=days_entry_gated)
