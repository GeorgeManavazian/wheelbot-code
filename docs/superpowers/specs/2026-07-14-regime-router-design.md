# Regime Router (Regime Bot v1) — design

- **Date:** 2026-07-14
- **Status:** drafting — becomes the pre-registration when committed; rules frozen before any test
- **Project:** Regime Bot (vault `09 Regime Bot` — a SEPARATE project from the wheel bot; founding brief and decision record live there). Code shares `code/etf-bot`.
- **Owner decisions locked (design dialogue 2026-07-14):**
  1. v1 strategy menu (below); bear call spreads in the quiet-decline cell are v2.
  2. Border handling = Approach A: finish what you started; no forced exits on regime flips (single exception below).
  3. Universe: seen tickers only (SPY GDX SLV XOP), run independently at $100k each; XOP from 2020-07-01.
  4. Judged against BOTH buy-hold and solo wheel+basis, raw, per ticker; owner judges; no kill rule.

## The routing table (fixed, zero knobs)

Ticker regime state (phase-1 `regime_series`: trend × vol, fixed ex-ante thresholds, inherited UNCHANGED) read strictly-prior-day with the 14-day staleness bound — the house information rule.

| Ticker state | Cell | Posture |
|---|---|---|
| uptrend (any vol) | TREND | hold shares (100-lots) |
| chop (any vol) | WHEEL | wheel + basis floor |
| downtrend + stressed | WHEEL | wheel + basis floor (panic premium) |
| downtrend + calm/normal | CASH | no new positions |
| unknown / warmup / stale | WHEEL | wheel + basis floor (the proven incumbent; "hold" must be earned by a proven uptrend) — logged `route_state_unknown` |

One sentence of economics per row: trends are held because the wheel's entire deficit is sold upside; chop and panic are wheeled because premium is the only thing chop pays and panic premium is the wheel's best-proven cell; quiet declines pay nobody, so nothing is opened there.

## The state machine (Approach A — the core of this spec)

Postures: **CASH** (flat), **TREND** (long shares bought as a trend position), **WHEEL** (the solo wheel's own sub-state: short put / short call / assigned shares with basis + campaign premium). The router consults the cell **only at decision points**, never to force-close.

**Daily order (EOD):** 1) manage any open short by the solo wheel rules (TP → expiry resolution — these never consult the cell); 2) posture transitions and entries per the rules below; 3) mark equity.

### Entries (only when flat in cash)

- Cell TREND → `BUY_SHARES`: lots = cash // (spot × 100), fill at EOD spot (no stock spread modeled — disclosed). A trend holding opens its own campaign id.
- Cell WHEEL → sell a put by the solo entry rules (selection, sizing, churn block unchanged).
- Cell CASH → nothing; the day is counted (`days_cash`).

### Transitions with an open position

1. **Wheel short leg open, any flip:** the leg runs to its natural end (TP / expiry / assignment). No forced closes — a regime flip is not an exit signal. After the campaign returns to flat cash, the router routes fresh.
2. **TREND shares held, flip to downtrend (either vol):** `SELL_SHARES` at next close — the single forced exit in the design. The trend was the only reason to hold; state says it is dead. (Note the asymmetry with wheel shares, and why: trend shares were bought at trend prices with no premium cushion; their thesis is the trend itself.)
3. **TREND shares held, flip to chop:** keep the shares and hand them to the wheel: begin covered calls under the basis floor with **basis = the shares' purchase price** and campaign premium accrued from zero. From this moment they are wheel shares (rule 5 owns them). Economic sentence: chop is rent-collection weather, and the floor already guarantees rent never locks a loss.
4. **WHEEL assigned shares (siege), flip to uptrend:** keep the shares, pause call-writing (the uptrend cell holds, it does not rent). The wheel's wound becomes the router's feature: crash-assigned shares ride the recovery uncapped. Basis and campaign premium are retained for when call-writing resumes.
5. **WHEEL shares in any non-uptrend cell:** solo wheel rules, unchanged — covered calls under the basis floor in WHEEL cells; in the CASH cell, no new calls are written (no new positions of any kind) but the shares are **held, never regime-sold** (the siege-exit falsification is binding precedent: selling waiting shares in quiet declines sells bottoms).
6. **Call-writing permission, in one line:** covered calls may only be OPENED in WHEEL cells. An open call always runs to its natural end (rule 1).

### Sequencing detail (pinned to avoid ambiguity)

Within one day's step 2, transitions resolve before entries: a `SELL_SHARES` (rule 2) frees cash that the entry rules may redeploy the SAME day if the cell says so (e.g. uptrend → downtrend+stressed: shares sold at close, a put may be sold that close into panic premium). Same-day redeploys use the same strictly-prior-day state as everything else; no intra-day information exists at EOD granularity.

### Consequences worth stating (not extra rules — implications)

- A put sold in chop can be assigned during a later panic; if the state then turns uptrend, rules 4+1 compose: the shares ride the trend. No special case needed.
- Chop-around-the-200d-line produces alternating TREND buys and (on downtrend flips) sells — whipsaw. It is measured and disclosed (whipsaw counter in the A/B protocol), never tuned away: there is no hysteresis knob, because the regime definition is inherited and frozen.
- The router holds at most one position type at a time per ticker; there is no leverage and no netting.

## Config

Frozen basket config, basis floor always on: 20Δ both legs, target 7 DTE, 50% TP, $100k, real commissions/spreads on options, cash yield 0, `call_min_strike="basis"`. **Zero new tunables** — the routing table and transition rules above are code constants. EOD fills in v1.

## Module layout

One new module: `src/engine_v2/options/regime_router.py`. Nothing in `wheel.py` or `portfolio.py` changes. Since each ticker runs independently ($100k each), the entry point takes a single chain and a single states frame — no dicts, unlike the portfolio engine:

```python
def run_regime_router(chain: pd.DataFrame, cfg: WheelConfig,
                      regime_states: pd.DataFrame) -> RouterResult
```

**RouterResult** (dataclass, mirrors `WheelResult`/`PortfolioResult` conventions):

- `equity: pd.Series` — daily mark, same semantics as solo (cash + shares×spot − short liability at last mid).
- `trades: list[Trade]` — reuses the `Trade` dataclass; `action` is a string, so `BUY_SHARES`/`SELL_SHARES` need no schema change (`contract=None`, `price_per_contract=` fill spot). A trend holding is its own campaign id.
- `warnings: list` — same `(date, kind, obj)` tuples as solo (`expiry_resolved_late`, missing-mark, state-unknown).
- `route_log: list` — one entry per day: `(date, trend, vol, posture)` — the regime cell that was read and the posture held. The audit trail for every routing decision; analogous to `route_events` in `PortfolioResult` but dense, not event-sparse.
- `days_in_posture: dict` — `{"CASH": n, "TREND": n, "WHEEL": n}` counters, plus the solo `days_flat` / `days_shares_uncovered` where the wheel posture is active.
- `final_cash`, `final_shares`, `residual_settled` — as in solo.

**Imports vs re-implements.** From `.wheel`: `WheelConfig`, `Trade`, `is_unpaid_decline`, `_state_before`, `sell_proceeds`, `buy_cost`, `GATE_STALENESS_DAYS`. From `.select`: `select_contract`, `option_mark`. The day-loop itself is the router's own — it wraps the solo wheel mechanics (TP → expiry → covered-call entry, verbatim ordering) in a posture dispatch, exactly as `portfolio.py` wraps them in a routing loop. Decision helpers are imported, never copied; the loop is never imported, because the solo loop has no seam for posture switching.

**Position state.** A posture enum `CASH` / `TREND` / `WHEEL`, plus:
- TREND: `shares` (100-share lots: `lots = cash // (spot*100)`), entry campaign id.
- WHEEL: the solo state tuple verbatim — `short` dict (contract, contracts, credit, last_mid), `shares`, `phase`, `basis`, `campaign_premium`. Matching solo field-for-field is what makes the equivalence hook checkable.

**Dependency direction.** `options/` may not import `regime/` — `regime/data.py` already imports `options/data.py`, so the reverse edge would be a cycle. States are computed by the caller and passed in as a DataFrame with `trend`/`vol`/`vol_pctile` columns. Precedent: the regime gates (`run_wheel(regime_states=...)`) and `run_portfolio_wheel(regime_states=...)` both take state this way; `GATE_STALENESS_DAYS` stays hand-synced for the same reason.

### Wheel-equivalence hook

With a states DataFrame that reads `chop` every day, the router must reproduce the solo `run_wheel` with `call_min_strike="basis"` **byte-identically** — same equity series, same trade list, same warnings. This is the regression anchor for the whole module, directly following the portfolio solo-equivalence precedent (one-ticker portfolio ≡ solo run). Any divergence under all-chop states is a router bug by definition, not a tuning question.

## Testing

New file `tests/engine_v2/options/test_regime_router.py`, mirroring the fixture style of `test_portfolio.py` / `test_regime_gates.py` (minimal synthetic chains via `_chain`, synthetic state frames via `_states`, zero-commission `_cfg`). All tests are written before the router; the suite is the spec's executable form.

**Wheel equivalence (the anchor test).**
- `test_all_chop_states_byte_identical_to_solo_wheel` — real SPY chain, states all-chop for the full window. Router output must equal solo `run_wheel` with `call_min_strike="basis"` byte-for-byte: identical `(date, action, contracts, price_per_contract, cash_after)` trade tuples, `equity.equals(...)`, identical `final_cash`.

**Trend basics.**
- `test_uptrend_buys_100_lots_at_eod_spot` — uptrend day produces one `BUY_SHARES` in 100-share lots at that day's EOD spot; cash debited exactly.
- `test_uptrend_holds_no_calls_written` — shares held across further uptrend days; no `SELL_CALL` ever appears (covered calls are wheel-cell-only).

**Border transitions (one test per edge).**
- `test_uptrend_to_downtrend_sells_next_close` — direct flip: `SELL_SHARES` at the next close after the strictly-prior-day state shows downtrend; never same-day.
- `test_uptrend_to_chop_keeps_shares_starts_covered_calls_at_purchase_basis` — no share sale; first `SELL_CALL` respects basis floor with basis = original purchase price, not current spot.
- `test_chop_wheel_campaign_survives_flip_to_expiry` — put opened in chop, state flips mid-life; campaign runs naturally to expiry/TP, no forced close.
- `test_panic_assignment_then_uptrend_flip_keeps_shares_no_calls` — downtrend+stressed wheel assignment, then uptrend: assigned shares kept (never regime-sold), call writing stops.

**Cash cell.**
- `test_cash_cell_no_entries_days_counted` — downtrend+calm/normal: zero entries of any kind; the CASH posture counter increments.

**Unknown / stale state.**
- `test_unknown_state_routes_to_wheel_and_warns` — stale (>14d) states: behaves as chop, `route_state_unknown` warning logged (unknown allows, never blocks silently).

**No-look-ahead (property).**
- `test_same_day_state_flip_ignored` — state flips ON the action day; strictly-prior rule uses the previous day's cell.
- `test_future_state_rows_do_not_change_decisions` — appending future state rows leaves the trade list identical.

**Validation.**
- `test_missing_states_raises` — router without a state series raises `ValueError`.
- `test_solo_only_flags_raise` — `roll_tested_puts`, `put_stop_mult`, `regime_*_gate`, `liquidate_assignment` all rejected.

Coverage folded into the engine_v2 gate (≥95%).

## Referee (execution audit)

Extend `scripts/audit_defense_execution.py` with `--router`. The referee re-derives every decision from raw data using its OWN local implementations (`_unpaid`, `_asof`, 14-day staleness, unknown→chop) — engine code is never imported for decision logic; agreement is the proof.

Checks, in order:

1. **Cell re-derivation.** For every trading day, compute the cell from the audit's `_asof` lookup over the same closes series the runner uses.
2. **Share actions on legal days only.** Every `BUY_SHARES` sits on a derived-uptrend day with no lot already held; every `SELL_SHARES` sits on a derived direct uptrend→downtrend transition day and touches only trend-bought lots — a `SELL_SHARES` against wheel-assigned shares is a mismatch (lot provenance reconstructed from the ledger, as `positions_from_trades` does for option legs).
3. **Options only in wheel cells.** Every `SELL_PUT`/`SELL_CALL` lands on a derived chop or downtrend+stressed (or unknown→chop) day.
4. **Leg walk.** Each wheel leg's TP (50% of credit, EOD ask) and expiry resolution re-derived per the existing leg-walk; timestamps must agree.
5. **Double-entry, both directions.** An action without a derived trigger is a mismatch; a derived trigger without an action is a mismatch. No held day unaccounted.

Exit 0 (`ROUTER EXECUTION VERIFIED`) is required before any router backtest result is cited, same rule as the existing `--hourly` and `--portfolio` modes.

## A/B protocol (pre-registered)

One run per ticker: SPY, GDX, SLV, XOP — each backtested independently on the full available history at EOD fills, $100k starting capital, frozen basket config, zero knobs. XOP's window starts 2020-07-01 (chain split-broken before that date); the truncated window applies identically to all arms so no arm gets a history advantage. These four tickers are already burned — every number this protocol produces is **in-sample** and will be labeled as such. The unseen basket tickers (XBI, EEM, EWZ, TLT, ARKK, QQQ) are reserved for the wheel bot's pre-registered basket run; the runner refuses them by hard allowlist and this project does not touch them until that run reports.

**Two benchmarks per ticker, both mandatory.** The router is judged against:

1. **Buy-hold the ticker** (same window, same capital). If the router can't beat this, "why not just hold shares" wins and the router is dead weight.
2. **Solo wheel+basis** at identical config and window (baselines on record: SPY +106.1k, GDX +155.6k, SLV +134.1k, XOP +136.5k). If the router can't beat this, routing does not earn its complexity over the strategy it routes to.

Beating one but not the other is a partial result, reported as such — no averaging across tickers, no aggregation that hides a per-ticker loss.

**Reported metrics per ticker:** total P&L ($), total return, CAGR, Sharpe, max drawdown, per-year return table, days spent in each posture (TREND / WHEEL / CASH), posture transition count, and **whipsaw count**. Whipsaw proxy, defined precisely and measurably: a `SELL_SHARES` fill occurring within 10 trading days of the `BUY_SHARES` fill that opened the share position. Each such pair is counted and reported. This is a disclosure, not a rule — no threshold triggers any action; the owner sees the number raw.

**Runner:** `scripts/run_regime_router.py`. **Output:** `data/options/reports/regime_router.txt`. One report, all four tickers, both benchmarks inline, per-year tables, no config variants.

### Expectations recorded ex-ante

Recorded before the run, per house rules. The founding evidence says buy-hold beat solo wheel+basis on every ticker (by +57k to +112k), meaning the wheel's chronic cost is capping trends. The router should therefore recapture some of that trend upside by holding shares in uptrends, landing **above the wheel** on most or all tickers. The known risk is whipsaw cost when price chops around the 200-day: repeated `BUY_SHARES`/`SELL_SHARES` round trips could bleed more than the wheel would have earned in the same stretch. Failure condition: router below **both** benchmarks on a ticker — routing destroyed value outright. Partial success: between the wheel and buy-hold — routing helps but doesn't close the gap. Full success — above buy-hold — is not expected ex-ante and would warrant skepticism, not celebration. Owner judges; no pre-committed kill rule.

## Dangers acknowledged at spec time

**In-sample rule design.** The router was designed on the same four tickers and the same 2017–2026 window that burned the wheel bot. Every rule here is, in that sense, fitted. Mitigation: each rule is a one-sentence economic statement fixed before any test runs, and backtest results on this window are consistency checks, not validation. Real validation waits for data unseen at design time, after the wheel's basket run releases it.

**Whipsaw.** The 200d line will get crossed and re-crossed, and each round trip costs a spread and possibly a trend-share sale near a local bottom. Accepted, not mitigated. The backtest reports the whipsaw counter defined in the A/B protocol. We do not add hysteresis, confirmation days, or buffer bands; any of those would be a tuned knob wearing a safety vest.

**Inherited regime definition.** The regime thresholds come from phase 1 unchanged. They will look improvable once results arrive. Re-fitting them inside this bot would be silent knob-turning, so they stay frozen: if the phase-1 definition is wrong, this spec inherits the error and reports it rather than papering over it.

**Survivorship in the hold-in-uptrend rule.** Holding shares through every uptrend worked from 2017 to 2026 because every uptrend in these four tickers resolved upward. A decade of false trends breaks the rule outright. We accept this and state it: the rule encodes a bull-decade prior, not a law.

**Asymmetric fill realism.** Stock trades fill at EOD spot with no spread; option trades pay the full quoted spread. This flatters the stock legs relative to the option legs. Disclosed, not corrected, since we lack intraday stock quote data to do better.

## Out of scope (v1)

- Bear call spreads in the downtrend-quiet cell. That cell holds cash in v1; spreads need a two-leg engine and are deferred to v2.
- Any portfolio or shared-cash-pool layer. Each ticker runs its own sleeve.
- Hourly or intraday fills. All decisions and fills are EOD.
- Tickers outside the four already studied.
- Per-regime parameters of any kind (delta targets, DTE, sizing). One parameter set everywhere.
- VIX or any external data feed. Regime state comes from price alone.
- Leverage and margin. Cash-secured throughout.

## References

- Founding: vault `09 Regime Bot/Founding brief — why this bot exists` + decision record 2026-07-14
- Inherited machinery: `2026-07-13-regime-advisor-design.md` (states), wheel engine + basis floor, portfolio precedent `2026-07-14-portfolio-rotation-design.md`
- Falsified precedents binding on this design: siege exit (`2026-07-14-regime-siege-exit-design.md`), stop thresholds (log 2026-07-14), rotation A/B
