# Design: Wheel sub-project 2 — the Wheel engine (pure wheel, EOD)

- **Date:** 2026-07-11
- **Status:** design — awaiting review
- **Part of:** the Wheel-on-SPY options backtester. Sub-project 2 of ~5.
- **Scope:** the pure-wheel state machine + EOD backtest loop → equity curve + trade log. Builds on sub-project 1's `OptionsChain` + primitives. NO rolling, NO intraday, NO reporting.

## Decisions (owner-confirmed 2026-07-11)

- **Pure wheel** (assignment-based; no rolling — a later parameter).
- **$100k** start, cash-secured, sell as many put contracts as collateral allows.
- **Take-profit 50%** default (close a short option once it decays to 50% of the credit; `None` = hold to expiry).
- **Cross the spread:** sell at **bid**, buy-to-close at **ask** (honest, pessimistic).
- Knobs to sweep later: put/call delta, DTE window, take-profit %.

## Inputs

1. **`OptionsChain`** (sub-project 1 tidy frame) covering the backtest window: for every EOD `date`, the strikes to select from AND the daily marks of any held contract until it exits/expires. (The sub-project-1 puller produces exactly this per-expiration.)
2. **Underlying series** — SPY EOD spot per date. Derived FROM the chain itself: `underlying_series(chain) = chain.groupby("date")["underlying"].first()`. This is self-contained and resolves sub-project 1's carry-forward (settlement reads the underlying series, not a specific contract's row — robust when a given contract's row is missing at expiry).

## `WheelConfig` (dataclass)

`starting_capital=100_000.0, put_delta=0.30, call_delta=0.30, dte_min=25, dte_max=45, take_profit_pct=0.50, contract_multiplier=100, commission_per_contract=0.65`.

## State machine (pure wheel)

Two phases; the engine is always in exactly one, holding at most one short option.

**PUT phase** (holding cash; possibly short a put):
- *No short put open* → open one: `select_strike_by_delta(chain, date, "P", put_delta, dte_min, dte_max)`. If `None` (no strike in window that day) → wait, try next day. Else SELL it: `contracts = floor(cash / (strike*mult))`; receive `bid*mult*contracts` cash. Record the entry credit/contract.
- *Short put open*, each subsequent date:
  - **Take-profit:** buy-to-close if `option_mark.ask ≤ (1 - tp) * entry_credit_per_contract`. Pay `ask*mult*contracts`; realize; → no-position (re-open next eligible day).
  - **Expiry** (`date == expiry`): read `expiry_underlying`. If `underlying < strike` (ITM) → **assigned**: pay `strike*mult*contracts` cash, receive `mult*contracts` shares at cost `strike`. → **CALL phase**. Else (OTM) → **expires worthless**: keep the credit, no cash change at expiry. → stay PUT phase (re-open next day).

**CALL phase** (own shares; possibly short a covered call):
- *No short call open* → `select_strike_by_delta(chain, date, "C", call_delta, dte_min, dte_max)`, `contracts = shares // mult` (covered). SELL: receive `bid*mult*contracts`.
- *Short call open*, each subsequent date:
  - **Take-profit:** buy-to-close if `ask ≤ (1 - tp) * entry_credit_per_contract`; keep shares; → re-sell a call next eligible day.
  - **Expiry:** if `underlying > strike` (ITM) → **called away**: sell shares at `strike*mult*contracts` cash, shares → 0. → **PUT phase**. Else → **expires worthless**: keep credit + shares; → re-sell a call.

Edge cases: if no strike is available in the DTE window on a given day, remain uncovered that day and retry (log it). If a held contract has no mark on a date (`option_mark` None), carry the previous mark for MTM and still resolve at expiry via the underlying (a gap must not crash the run).

## Equity accounting (daily, EOD)

`equity(date) = cash + shares * underlying(date) - short_option_liability(date)`
where `short_option_liability = mark_mid * mult * contracts` for an open short option (mark-to-mid for the curve; **trades realize at bid/ask**, not mid). No short option → liability 0. This gives a daily equity series over the window.

Realized cash flows (the only things that move `cash`): sell-to-open (+bid×mult×contracts − commission), buy-to-close (−ask×mult×contracts − commission), assignment (−strike×mult×contracts), called-away (+strike×mult×contracts). **Commission = `commission_per_contract × contracts`, charged on every option OPEN and CLOSE trade** (default $0.65/contract — Schwab's options rate; unlike free ETF stock trades). Assignment / called-away themselves incur no commission (Schwab charges $0 for assignment/exercise).

## Fills

`sell_fill(mark) = mark.bid`, `buy_fill(mark) = mark.ask`, both × `mult` × `contracts`, then **minus `commission_per_contract × contracts`** on the trade. Crossing the spread every trade + the per-contract commission. (A slippage layer beyond the spread is a later refinement.)

## Output — `WheelResult`

- `equity: pd.Series` (date → equity).
- `trades: list[Trade]` — each: `date, action ∈ {SELL_PUT, CLOSE_PUT, ASSIGNED, PUT_EXPIRED, SELL_CALL, CLOSE_CALL, CALLED_AWAY, CALL_EXPIRED}, contract, contracts, price_per_contract, cash_after`.
- `final_cash, final_shares`.
No CAGR/Sharpe/drawdown here — that is sub-project 5 (reporting), which will reuse the existing frequency-aware `metrics_simple`.

## Where code lives

New module `src/engine_v2/options/wheel.py`:
- `WheelConfig` dataclass; `Trade` dataclass.
- `underlying_series(chain) -> pd.Series`.
- `sell_fill` / `buy_fill` helpers.
- `run_wheel(chain, config) -> WheelResult` — the daily loop + state machine.

Imports only sub-project 1's `select.py`/`chain.py` + pandas. NOT the equity `_simulate`, gate, or `run_backtest`.

## Testing

**Synthetic chains (deterministic, no network) — one per state transition:**
- Put sold, expires OTM → keep credit, equity rises by credit; state stays PUT.
- Put sold, ITM at expiry → assigned; cash drops by strike×mult, shares appear at cost, state → CALL.
- Covered call sold, called away → shares sold at strike, state → PUT; verify a full cycle's P&L.
- Take-profit: option mark falls to 50% → buy-to-close fires; realized P&L = credit − ask.
- Sizing: `contracts = floor(cash / (strike×mult))` at a known cash/strike.
- Fills: sell realizes bid, buy-to-close pays ask (not mid).
- No-strike-available day → no crash, retries next day.
- Equity identity: `cash + shares*underlying − short_liability` reconciles to the running equity at several dates.

**Golden integration (real data):** a small real SPY chain pull (one ~30-45 DTE monthly cycle, e.g. a 2024 month) → run_wheel → assert a plausible equity path, a non-empty trade log with the expected event types, and equity finite/positive. Pinned to fixed values so refactors can't drift them. (Requires the terminal + a tight pull; the pulled fixture is committed small.)

## Non-goals (later sub-projects)

- No rolling (later parameter). No intraday take-profit timing (sub-project 4 — EOD approximates it here). No reporting/metrics (sub-project 5). No dashboard. No multi-underlying. No early assignment (American-exercise before expiry) — EOD/European-style resolution at expiry only; flagged as a simplification.
- Commission is a flat `$0.65/contract` per option trade (Schwab's rate). Exchange/regulatory per-contract fees and any slippage beyond the quoted spread are a later refinement.

## Open choices (defaulted; flag to change)

- **MTM at mid** for the equity curve (trades still cross the spread). Alternative: MTM at the conservative side (ask for shorts) — would depress the curve between trades; mid is the neutral choice.
- **DTE window 25–45** default (monthly wheel). Sweepable.
- **European-style expiry resolution** (no early assignment) — a real simplification for American SPY options, but early assignment is rare except deep-ITM/around dividends; flagged, revisit if results look off.
- **Assignment always taken when ITM at expiry** (pure wheel). Rolling is the later alternative.
