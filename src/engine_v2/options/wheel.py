"""Pure-wheel EOD backtest engine. Builds on the options chain + primitives.
No rolling, no intraday, no metrics (sub-project 5). Isolated from the equity
engine and the gate."""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from .select import (select_contract, select_roll_contract, option_mark,
                     liquidity_ok, at_risky_window_edge)
from .fills import try_take_profit, tp_exit_feasible, credit_ok, yield_ok

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
    # A13: pass-through fees (OCC clearing + ORF + SEC/TAF sell-side, all
    # itemized on a real statement as one blur) as ONE flat per-contract-side
    # adder, and the broker's per-EVENT assignment/exercise fee. Defaults 0.0
    # so the plain path is byte-identical (golden precedent); production sets
    # fees_per_contract=0.05 in live/run_daily.FROZEN (provisional -- owner
    # verifies against the first real statement; the audit's $0.30 implied
    # rate double-counts exchange fees Schwab embeds in the $0.65).
    fees_per_contract: float = 0.0
    fee_per_assignment: float = 0.0
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
    chop_max_fast_spread: float | None = None  # chop scanner: reject if |9d/20d-1| > this, symmetric (default off)
    chop_max_fast_fall: float | None = None    # chop scanner: reject if 9d/20d-1 < -this, down-only (default off)
    # liquidity gate (A2, 2026-08-01) — VETO on new short-put entries and roll
    # destinations only; never on closes, expiry, marks, or (by owner-
    # provisional decision) covered calls. Default-off here like every gate
    # above so the plain path stays byte-identical; production turns it ON in
    # live/run_daily.FROZEN. With a threshold SET, a missing/NaN field FAILS
    # (a contract whose liquidity cannot be measured is not one to sell);
    # backtest chains have no OI/volume columns, so backtest configs leave
    # those two legs None — a declared one-sentence divergence, like the A18
    # print-vs-quote modes.
    liq_max_rel_spread: float | None = None    # reject if (ask-bid)/((bid+ask)/2) > this
    liq_min_open_interest: float | None = None # reject if open_interest missing/NaN or < this
    liq_min_volume: float | None = None        # reject if volume missing/NaN or < this
    # Intrinsic filter. A short put whose credit is a large fraction of the
    # strike is not a volatility sale -- it is mostly INTRINSIC value, i.e.
    # buying the stock with extra steps while booking the purchase discount as
    # "premium collected". Measured on the 2.53y 500k run: 2 of 299 entries
    # exceeded 3% (NOK @ 37.2%, HL @ 9.4%) and those two carried -$47,150 of
    # the -$64,650 total share-leg loss -- 73% of the damage from 0.7% of the
    # trades. Median entry is 0.52% of strike, so a 3% cap is nearly inert.
    # None = off, so every existing result stays byte-identical.
    max_credit_pct_of_strike: float | None = None
    # Earnings blackout (spec 2026-08-03). VETO on new short-put entries when a
    # scheduled print falls inside [obs_date, expiry + 1 day]. Puts only --
    # covered calls are exempt for the same reason the A2 liquidity gate exempts
    # them: refusing a call leaves assigned shares honestly naked, which is
    # worse. A boolean, not a window: the +1 day is the after-close correction
    # (see earnings.BLACKOUT_BUFFER_DAYS), not a tuning surface. Default False
    # like every gate above, so the plain path stays byte-identical. Needs the
    # market to supply earnings_dates(); without it the gate is inert.
    earnings_blackout: bool = False
    # Collateral-yield floor (spec 2026-08-04). VETO on new short-put entries
    # whose credit is a negligible return on the cash the strike locks up:
    # (credit/strike) * (365/dte) < this. The pre-existing minimum credit
    # (tp_exit_floor) is ABSOLUTE -- $2.50/contract at the live config -- so a
    # $1,000-strike put locking $100,000 clears it on $2.50. Measured medians
    # over 2024-01 -> 2026-07 are 18.3%/30.9%/45.6% annualised at 0.20/0.30/
    # 0.40 delta, and the least generous real name (XLU) is 8.4%, so 0.08 is a
    # junk filter rather than a tuning surface. None = off, byte-identical.
    min_ann_yield_on_collateral: float | None = None
    # IV-rank floor (measured 2026-08-04). VETO on new short-put entries whose
    # implied vol is cheap against that same ticker's own trailing 252
    # observations -- Natenberg's relative volatility rank, p.72-74: "when
    # relative volatility is high (8-10), focus on SELLING premium." This is
    # the first rule in the engine that reads `iv` at all; every existing gate
    # measures risk, none measures the price paid for it. Over 42,226 gated
    # ticker-days on 153 names (2024-01 -> 2026-07, delta 0.40 / DTE 5-10 /
    # TP 25%), mean return on collateral is -11.8 bps overall and crosses zero
    # only around rank 0.85-0.88, reaching +13.7 bps at 0.90 and +19.7 at 0.95
    # -- a monotone ramp, positive in 2024, 2025 and 2026 separately.
    #
    # MEASURED AND REJECTED AS A VETO (2026-08-04). On the real portfolio it
    # raises P&L per campaign ~14% and HALVES campaign count, costing 18 points
    # of total return (+43.0% -> +24.9% at 0.88) and 0.26 of Sharpe. The signal
    # is real; the veto is the wrong instrument, because the wheel's return
    # comes from capital turnover. Kept default-off as the measurement
    # instrument for the open ranking question -- see the vault note
    # "IV rank as an entry veto -- measured, rejected". Do not switch this on
    # without re-reading it.
    #
    # Needs the market to supply iv_rank(); without it the gate is inert.
    # None = off, so every existing result stays byte-identical.
    min_iv_rank: float | None = None
    # IV rank as the SORT KEY rather than a veto (owner approved 2026-08-05,
    # after min_iv_rank above was measured and rejected). The pool is ranked
    # by `vol_pctile` -- REALIZED vol, highest first -- so the engine picks the
    # riskiest name and never reads the price it is paid for that risk. Worse,
    # the chop weather gate has already capped realized vol at the 75th
    # percentile, so the sort chooses inside a 70-75th percentile sliver on
    # the wrong variable.
    #
    # A sort is the right instrument where a veto was the wrong one: it
    # changes WHICH names fill the slots, never HOW MANY, so it does not pay
    # the turnover cost that halved campaign count (254 -> 130) and cost 18
    # points of return. Unknown ranks sort at NEUTRAL_IV_RANK, never last --
    # see iv_rank.py on why last would rebuild the filter.
    #
    # "vol_pctile" = off, so every existing result stays byte-identical.
    rank_by: str = "vol_pctile"

    @property
    def any_regime_gate(self) -> bool:
        return self.regime_entry_gate or self.regime_roll_gate or self.regime_stop_gate

    @property
    def friction_per_contract(self) -> float:
        """A13: the one per-contract-side cost every fill and every guard
        prices. sell_proceeds / buy_cost / try_take_profit / tp_exit_floor all
        consume THIS, never the raw commission -- if the fills charge it and
        the floor doesn't (or vice versa), the bot either writes guaranteed-
        loss exits or refuses survivable ones."""
        return self.commission_per_contract + self.fees_per_contract

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
            - cfg.friction_per_contract * contracts)

def buy_cost(mark, contracts, cfg) -> float:
    return (mark.ask * cfg.contract_multiplier * contracts
            + cfg.friction_per_contract * contracts)

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
        # A9: an assignment booked this session defers the covered call to the
        # next stepped session (notice after the close, shares settle T+1). A
        # local flag suffices here -- the batch engine steps each date exactly
        # once; the persisted twin lives in portfolio.py for the live path.
        assigned_today = False

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
            # take-profit: the shared fill seam (fills.py). Print-next-bar mode
            # when this contract has hourly bars, else the EOD quote at the ask.
            # All the rules (close>0 prints only, fill at bar i+1, last-bar
            # cross falls through to the quote, >=1.0 = hold to expiry) live in
            # try_take_profit -- one copy for all four engines (A18).
            key = (pd.Timestamp(c.expiry), float(c.strike), c.right)
            dec = try_take_profit(mark=mark, credit=short["credit"], contracts=n,
                                  cfg=cfg, day=d, expiry=c.expiry,
                                  bars=intraday.get(key) if intraday is not None else None)
            if dec.filled:
                cash -= dec.cost; campaign_premium -= dec.cost
                trades.append(Trade(dec.stamp,
                                    "CLOSE_PUT" if c.right == "P" else "CLOSE_CALL",
                                    c, dec.filled_contracts, dec.price, cash, campaign))
                # A17: decrement the live size; both current modes fill whole,
                # so this reaches zero exactly as the old `short = None` did
                short["contracts"] -= dec.filled_contracts
                if short["contracts"] <= 0:
                    short = None; closed_today = c
            if short is not None:
                n = short["contracts"]   # re-read after a (possibly partial) TP fill (A17/I3)
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
                    # A2: a roll OPENS a new leg; its destination faces the same
                    # liquidity veto as an entry. The close half is untouched.
                    # Never silent (A3 skeptic F4): a refused roll runs the leg
                    # to expiry, and the log must say why.
                    if new_c is not None and not liquidity_ok(day_chain, d, new_c, cfg)[0]:
                        warnings.append((d, "roll_gated_illiquid", cfg.ticker))
                        new_c = None
                    new_mark = option_mark(day_chain, d, new_c) if new_c is not None else None
                    # A3: the destination's own TP exit must be feasible too --
                    # rolling INTO an unclosable leg is opening one.
                    if new_mark is not None and not tp_exit_feasible(new_mark.bid, cfg)[0]:
                        warnings.append((d, "roll_gated_unclosable", cfg.ticker))
                        new_c, new_mark = None, None
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
                        # A13: per-event assignment fee ($0 at Schwab, VERIFY)
                        cash -= c.strike * mult * n + cfg.fee_per_assignment
                        shares += mult * n; phase = "CALL"
                        basis = c.strike
                        assigned_today = True   # A9: no covered call this session
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
                        cash += c.strike * mult * n - cfg.fee_per_assignment
                        shares -= mult * n; phase = "PUT"
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
                liq = (c is None or c == closed_today or mark is None
                       or liquidity_ok(day_chain, d, c, cfg)[0])
                if not liq:
                    # A2 skeptic F2: a veto must never be silent -- a gated
                    # backtest day is otherwise indistinguishable from a
                    # no-weather day when attributing days_flat.
                    warnings.append((d, "entry_gated_illiquid", cfg.ticker))
                if liq and c is not None and c != closed_today and mark is not None \
                        and not tp_exit_feasible(mark.bid, cfg)[0]:
                    # A3: the entry's own TP exit is unsatisfiable/net-negative
                    warnings.append((d, "entry_gated_unclosable", cfg.ticker))
                    liq = False
                if liq and c is not None and c != closed_today and mark is not None \
                        and not credit_ok(mark.bid, c.strike, cfg)[0]:
                    # Intrinsic filter: the credit is mostly moneyness, not
                    # volatility. Veto like A2/A3 -- never substitute a
                    # different strike, and never silently.
                    warnings.append((d, "entry_gated_intrinsic", cfg.ticker))
                    liq = False
                if liq and c is not None and c != closed_today and mark is not None \
                        and not yield_ok(mark.bid, c.strike,
                                         (c.expiry - d).days, cfg)[0]:
                    # Collateral-yield floor: this credit is a negligible
                    # return on the cash the strike locks up. Veto like
                    # A2/A3 -- never substitute, never silently. dte comes
                    # from the SELECTED contract's expiry, not target_dte.
                    warnings.append((d, "entry_gated_low_yield", cfg.ticker))
                    liq = False
                if c is not None and c != closed_today and mark is not None and liq:
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
                            if at_risky_window_edge(day_chain, d, c, cfg.put_delta):
                                # B11: clipped window -- riskier than configured
                                warnings.append((d, "strike_window_edge", cfg.ticker))
            elif phase == "CALL" and shares >= mult and not assigned_today:
                floor = None
                if cfg.call_min_strike == "basis" and basis is not None:
                    # net basis: assignment strike minus premium already banked
                    # this campaign — the anchor ratchets DOWN as rent comes in.
                    floor = basis - campaign_premium / shares
                c = select_contract(day_chain, d, "C", cfg.call_delta, cfg.target_dte,
                                    cfg.ticker, min_strike=floor)
                mark = option_mark(day_chain, d, c) if c is not None else None
                feasible = mark is None or tp_exit_feasible(mark.bid, cfg)[0]
                if mark is not None and not feasible:
                    # A3b: covered call with an unsatisfiable TP exit -- refused
                    warnings.append((d, "call_gated_unclosable", cfg.ticker))
                if c is not None and c != closed_today and mark is not None and feasible:
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
