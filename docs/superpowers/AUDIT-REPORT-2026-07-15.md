# Chameleon deep audit — 2026-07-15 (read-only)

Owner-requested fresh-context audit of the entire Chameleon bot: weather/regime, wheel engine, Chameleon router, referees. Method: four independent fresh-eyes code auditors reading each module COLD (no chat assumptions) + full empirical stress (all referees, full suite, coverage, no-look-ahead property on real data, known-date checks). This audit is READ-ONLY — findings recorded, merged code NOT patched.

## Headline

**The bot is working correctly.** Across four cold code audits and the full empirical battery, **zero confirmed correctness defects in the decision/trading path.** Every planted-bug class (look-ahead, cash mis-accounting, posture holes, referee circularity, sizing/counter errors) was hunted and found ABSENT. A handful of latent-robustness and design notes are below — none affects any shipped result.

## Empirical stress — ALL GREEN

| Check | Result |
|---|---|
| Default referee (roll/stop/gate variants, 4 tickers) | exit 0 |
| `--hourly` referee (intraday TP fills re-derived) | exit 0 |
| `--portfolio` referee | exit 0 |
| `--router` referee (routes + share actions + legs + conviction-trim half-sizing) | exit 0 |
| Full test suite | 332 passed |
| Coverage | wheel.py 100%, state.py 100%, regime_router.py 97%, total 94% |
| No-look-ahead property on REAL GDX (210 trades) | full-vs-truncated states identical |
| Trim-off byte-identical, all 4 seen tickers | identical |
| Known-date regime (2020-03-20 stressed / 2022-06-15 downtrend) | matches pinned tests, no drift |

## Module audits (fresh-eyes, cold)

### 1. Weather / regime (`state.py`, `base_rates.py`, `autopsy.py`, `data.py`) — CLEAN
- **Look-ahead proven absent empirically:** recomputing `regime_series` from `closes[:t+1]` reproduces the full-series label at every sampled t, 0 mismatches. Every window trailing; `rolling.rank(pct=True)` ranks the last (current) element — self-inclusive, never forward. First row at index 272 (matches docstring).
- Thresholds correct and consistent (strict >/<, ties→chop/normal); vol applied to `vol_pctile` not raw vol; warmup slice drops all partial-window rows; `autopsy` staleness + strictly-prior lookup correct.
- **Latent note (not a live bug):** `data.py:12-19` bars-path returns closes with `dropna().sort_index()` but no de-dup, unlike the chain path's `groupby("date").first()`. A bars parquet with a duplicate date would shift every rolling window by one bar. Not triggered on shipped data (SPY fixture has 0 dup dates). Cheap hardening: `s[~s.index.duplicated()]` on the bars branch. **Owner decision — trivial, optional.**

### 2. Wheel engine (`wheel.py`, `select.py`, `report.py`) — CLEAN
- Full campaign traced (sell put → assign → covered call → called away): every leg debits/credits correctly with commission + multiplier; reconciles to the penny. Equity mark consistent (spot for shares, mid for short liability), no double-count.
- No look-ahead (EOD marks; intraday TP fills next bar; expiry moneyness uses last close ≤ expiry). Zombie-expiry resolves on first day ≥ expiry across gaps, no phantom assignment.
- Basis floor = strike − campaign_premium/shares, ratchets correctly, never sells below net basis, no div-by-zero. Gates log at would-act moments only; denied roll does NOT consume the stop; unknown→allow+warn.
- Sizing (cash//(strike·mult) puts, shares//mult calls) guarantees assignment affordability. Integer edges clean.
- Design notes (documented, not bugs): liquidate fills at EOD spot on late resolution; `GATE_STALENESS_DAYS=14` hand-synced to autopsy (maintenance hazard, not a bug).

### 3. Chameleon router (`regime_router.py`) — CLEAN
- **Byte-identical invariant verified on ALL 9 tickers** (not just the SPY anchor): all-chop states ≡ solo `run_wheel(basis)` — trades, per-day equity, final cash identical everywhere, incl. tickers with data gaps/residuals.
- Posture machine hole-free: `trend_shares` and wheel `shares` provably mutually exclusive every day; `trend_trimmed`/`trend_buy_idx` reset on BOTH paths that zero trend_shares; campaign ids never reused; short never open while trend_shares>0.
- Cash/equity no double-count (mutual exclusion makes `(shares+trend_shares)*spot` safe); conversion cash-neutral with basis=purchase price; whipsaw boundary exact (≤10 days). Counters correct. Conviction-trim edges correct (single-lot→no buy; touches only BUY_SHARES).
- Scope notes (not bugs): router is EOD-only by design (no intraday param — correctly benchmarked against EOD wheel); trend shares are held (frozen) through an unknown-state stretch per Approach A.

### 4. Referees (`audit_defense_execution.py`) — SOUND (independence verified)
- **No circularity:** the referee does NOT import the engine's decision helpers. It re-derives every decision with its OWN local copies — `_unpaid`, `_asof`, `_asof_row`, `_cell_local` (confirmed: no `_state_before`/`is_unpaid_decline`/`_cell` import; the 2026-07-14 circularity fix, which flipped the router-audit from engine imports to local copies, holds). `run_wheel`/`run_regime_router` are imported only to GENERATE the ledger under test — correct.
- **Faithful independent restatement:** `_cell_local` restates the routing table (uptrend→TREND; downtrend+stressed→WHEEL; downtrend-else→CASH; chop/unknown→WHEEL) as separate code matching the engine's `_cell` — a genuine second implementation, so a bug in either breaks the agreement rather than hiding.
- **Double-entry, all modes:** each mode checks both directions (action-without-trigger AND trigger-without-action) and every held day is accounted; the router day-walk was hardened 2026-07-14 (chronological, silent trend→chop conversion re-derived per day). Conviction-trim block reconstructs full lots robustly (cfg.contract_multiplier + 1e-9 floor guard, fixed this session).
- **Empirically:** all four modes exit 0 (default, --hourly, --portfolio, --router) on all applicable tickers.
- **Honest boundary (not a defect):** the referee shares `regime_series` as INPUT with the engine (it does not re-implement the MA/percentile math). That is acceptable — `regime_series` is the shared upstream input (like the chain data), and it was independently proven look-ahead-free and threshold-correct by the weather-module audit above. The DECISION logic (how states become trades) is what the referee independently re-derives, and does.

## Bottom line for the owner

Chameleon — weather report, wheel engine, router, and conviction trim — is **correct and stress-verified**. Nothing in the trading path is broken. The only actionable item surfaced is one **optional, trivial** robustness hardening (bars-path date de-dup in `data.py`), which affects no shipped result and no seen-ticker run. Everything remains in-sample on burned tickers; correctness ≠ profitability, and the out-of-sample verdict still waits on the basket run.
