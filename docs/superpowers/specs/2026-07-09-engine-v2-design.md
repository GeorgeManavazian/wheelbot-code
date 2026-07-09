# ETF Bot — Engine v2 Design

**Date:** 2026-07-09
**Author:** brainstormed with Claude
**Status:** draft, pending user review
**Supersedes engine work in:** `2026-07-05-research-engine-design.md` (v1)

---

## Motivation

Two screenings (51 configs, 2010–2020 playground) proved textbook long-only factors have no edge — nothing beat 60/40 (Sharpe 1.37) or the cumulative luck line (0.85). The 2026-07-06 pivot reframed the bot's mission: active systematic **trading** (swing + day, long/short), **options as endgame**, edge-thesis-first.

The v1 engine (daily bars, monthly rebalance, long-only, single-split) cannot support that mission. Deeper problem: the honesty gate is incomplete. Every gold-tier reference book (Carver *Leveraged Trading*, Chan *Machine Trading*, Lopez de Prado *ML for Asset Managers*) demands machinery v1 lacks — walk-forward with purged CPCV, Deflated Sharpe with fat-tail deflation, family-wise error control, denoised covariance, real cost models, Carver sizing.

Engine v2 builds that machinery **before** any new strategy is tested, so every future leg (swing, day, options) plugs into the same rigorous gate.

## Decisions locked during brainstorm

1. **Attack order: Engine v2 first**, then swing/day/options plug in as strategies. No new strategy work during v2 build.
2. **First strategy leg after v2: undecided** — pending deep-research output (in flight since 2026-07-06). V2 built leg-agnostic with clean plugin interface.
3. **Modern-era data window: 2007+** (post-Reg NMS, post-decimalization). Purpose = testing plumbing + sizing math + drawdown code against real crashes (2008, 2020, 2022), NOT proving edge persistence. Edge decay handled separately by walk-forward + exam split + year-by-year table (existing decision, 2026-07-05).
4. **Fat v2 (all 8 additions before any strategy)**, not thin v2, not parallel scratch work. Rationale: solo dev, one honesty gate for bot's lifetime, no re-running backtests when machinery arrives later.
5. **Flexibility:** design can be restructured if a better shape surfaces during implementation or if deep-research candidates demand hooks not yet planned.

## Scope

**In scope**
- Backtest engine capable of daily-bar strategies, long AND short, with real cost model
- Walk-forward + purged/embargoed CPCV
- DSR + FWER promotion gate with per-regime breakdown
- Carver sizing wrapper (target-risk/instrument-σ, half-Kelly cap, IDM, LIFO de-risk)
- Denoised-covariance + NCO allocator (for multi-instrument portfolios)
- Modern-era data ingest (2007+), regime tagging, sealed playground/exam splits
- Same-code-path guarantee between backtest execution and future live executor
- Extension to existing Streamlit dashboard for verdict panel

**Out of scope (deferred to later legs)**
- Minute-bar data + intraday session model (day-trading leg)
- Options chains, Greeks, assignment (options leg)
- Live executor wiring to Schwab Trader API (post-v2)
- Broker outage / API drift handling (executor phase)

## Architecture

Five modules and one gate.

```
Data Layer → Strategy Plugin → Sizing Wrapper → Execution Sim
                                                     ↓
                            Validation & Promotion Gate
                                     ↓
                                 Dashboard
```

Isolation guarantees:
- Strategy plugin has no knowledge of sizing or execution.
- Sizing wrapper is strategy-agnostic (Carver stack applied identically to every plugin).
- Execution sim consumes sized orders, produces fills.
- Gate consumes fill-level P&L, produces verdict.
- Any one module can be swapped without touching the others.

**Repo layout**

- Existing v1 preserved under `src/engine/` until v2 replicates v1 numbers byte-identical on same inputs (regression baseline).
- v2 code under `src/engine_v2/`.
- Repo: `~/Documents/Trading code/etf-bot/`.

## Components

### Data Layer

**Inputs**
- Schwab price history API (canonical, same feed as live)
- yfinance (bootstrap only, checksum-validated against Schwab)
- ThetaData (stub interface only; not activated until options leg)
- Regime source: SPY 200d MA (bull/bear), VIX level (calm/high-vol), 10y–3mo yield (rising/falling rates)

**Outputs**
- `bars.parquet` — OHLCV + adjusted close, daily, 2007-01-01 → present
- `regime.parquet` — per-day tags `{regime_trend, regime_vol, regime_rate}`
- `splits/`: playground 2007–2020 / exam 2021–present, checksums in `splits/SEALED.txt`

**Rules**
- Fail loud on missing bar, split-unadjusted price, dividend gap. No silent fill-forward.
- Modern-era boundary: reject queries before 2007-01-01.
- `--exam` flag required to touch exam split; every access appended to `exam_access.log`.

### Strategy Plugin Interface

```python
class Strategy(Protocol):
    display_name: str        # mandatory, plain language
    mechanism: str           # mandatory, why edge persists
    parameter_grid: dict     # for CPCV trial expansion

    def forecast(self, bars: DataFrame, asof: Timestamp) -> Series:
        """Returns per-instrument forecast in [-20, +20].
        No sizing. No execution. No look-ahead."""
```

**Rules**
- Forecast range fixed [−20, +20], mean magnitude 10. Enables binary → non-binary migration without code change.
- No `self` state persisted across bars (pure function of bar window).
- Missing `mechanism` or `display_name` = engine refuses to load plugin.

### Sizing Wrapper (Carver stack)

Pipeline applied to every strategy:

```
forecast (±20)
  → risk-adjust (÷ instrument_σ)
  → rescale to mean 10
  → cap ±20
  → notional = target_risk × equity ÷ instrument_σ × forecast/10
  → apply IDM (Carver Table 43 lookup)
  → check speed limit (risk-adj cost ≤ 0.08)
  → LIFO de-risk check
  → sized order
```

**Config knobs**
- `target_risk` (default 12%, half-Kelly on SR 0.24 per Carver Ch.5)
- `account_cap` (25% multi-inst, 30% multi-rule, 30% hard ceiling)
- `idm_ceiling` (2.5 multi-asset, 1.4 single-asset — Carver Ch.7)
- `min_deviation_to_trade` (10% of avg exposure — Carver Ch.10)

### Execution Sim

**Cost model per order**
- Market order: cross full bid-ask spread at bar close (Chan Ch.6)
- Limit order: fill at midprice IF (bid ≤ limit ≤ ask), else no fill (Chan Ch.5)
- Slippage curve: `slip_bps = a + b × sqrt(order_notional / ADV)`, calibrated per instrument
- Short-specific: subtract `borrow_fee_bps / 252` daily; reject if HTB flag set

**Halt / gap handling**
- Stop breach on halt day → fill at next open (not intraday)
- Gap > 5% → log flag, still fill (real broker behavior)

**Same-code-path rule** (Chan Ch.1): live Schwab executor stub reuses this simulator's order-to-fill translation. Not two separate execution stacks. Verified by CI test.

### Validation & Promotion Gate

**Stage 1 — Walk-Forward CPCV**
- History chopped into N=10 folds
- Purge = max label horizon in days; embargo = same
- All (N choose k) train/test combos for k=2
- Per trial: fills → P&L → per-trade returns

**Stage 2 — Trial family statistics**
- K = total trials from `parameter_grid` × CPCV combos
- `E[K_effective]` via ONC clustering on trial-return correlation matrix (Lopez Ch.8)
- DSR computed with measured skew/kurt, not Normal (Lopez Ch.8, avoids halved α_K)
- FWER α_K = 1 − (1−0.05)^K_effective

**Stage 3 — Per-regime breakdown**
- Report Sharpe / max DD / Calmar in each of 8 regime cells (bull/bear × calm/high-vol × rising/falling rate — collapsed where sample thin)
- Kill rule: any regime with sample ≥20% of history showing negative Sharpe → SHELF, regardless of overall metrics

**Stage 4 — Verdict**

```
PASS  (→ paper)  if:  DSR > 0.95  AND  FWER < 0.05  AND  no negative-Sharpe regime  AND  Calmar_overall ≥ 1
WATCH (→ log)    if:  DSR ∈ [0.80, 0.95]  OR  one weak regime
SHELF            otherwise
```

**Exam gate**
- Passing paper → single exam-split run → same verdict formula
- One shot per strategy. Second look = strategy is dead.

## Data flow

End-to-end trace of one backtest:

1. **Ingest (once)** — Schwab API → `bars.parquet` (2007+), regime source → `regime.parquet`, checksums to `splits/SEALED.txt`.
2. **Trial expansion** — `strategy.parameter_grid × CPCV folds → K trials`; `trial_id = hash(strategy_name, params, fold_id)`.
3. **Per-trial backtest loop** — for each `asof` in test slice: slice data up to `asof - embargo`, strategy produces forecast, sizing wrapper produces sized orders, execution sim produces fills, equity updated, everything logged.
4. **Trial aggregation** — per-trade returns and per-regime returns computed and stored to `results/{run_id}/trials.parquet`.
5. **Gate computation** — pivot to trial-return matrix, cluster with ONC to get K_eff, compute DSR + FWER, compute per-regime Sharpe, apply verdict logic.
6. **Result persistence** — write `manifest.json`, `trials.parquet`, `verdict.json`, `SEALED` sentinel. Files chmod 444 after write.
7. **Dashboard reads** — Streamlit reads `verdict.json`, leaderboard filters by `verdict.pass`, run-detail shows regime cells + DSR + FWER.

### Flow invariants

1. Ingest is sealed. Re-ingest = new checksum = engine refuses to reuse stale results.
2. No look-ahead: data layer's `slice(end=asof)` clips to `asof - embargo`. Enforced in data layer, not strategy.
3. Trial log is append-only. Full replay possible from log alone.
4. Gate reads only trial log. Zero coupling back to strategy code. Formula change = re-run gate without re-backtesting.
5. Exam split isolated. `--exam` flag + log entry in `exam_access.log` required for every access.
6. Verdict is immutable. `SEALED` sentinel prevents overwrite. Rerun = new `run_id`.

### Failure modes traced through flow

| Where | Failure | Behavior |
|---|---|---|
| §1 | Missing bar | Fail loud, halt ingest |
| §3 | Strategy raises | Log trial as `errored`, continue other trials |
| §3 | HTB on short | Skip trade, log `htb_skip` |
| §3 | Halt day | Fill at next open, log `halt_fill` |
| §5 | Trial count K < 5 | Skip DSR, mark verdict `insufficient_trials` |
| §5 | Regime cell sample < 5% | Collapse into parent cell, note in verdict |
| §6 | Duplicate run_id | Reject write |

## Error handling & guardrails

### Error taxonomy

| Tier | Example | Response |
|---|---|---|
| **Fatal** | Missing bar, checksum mismatch, unsealed exam access, duplicate run_id, engine hash mismatch on rerun | Halt immediately, non-zero exit, no partial results written |
| **Trial-local** | Strategy raises in `forecast()`, HTB on short, halt day fill, gap >5%, zero-vol regime cell | Log with tag, continue other trials, mark trial `errored`/`skipped` in verdict |
| **Warning** | Regime cell sample <5%, K=1 trial, exam-split accessed, Sharpe estimated with <30 trades | Log warning, degrade gracefully, surface prominently in `verdict.json` |

### Hard-coded guardrails

1. **Same-code-path** — CI test compares fill vector from `execution_sim` on historical bars vs live-executor stub on same bars. Diff = red build.
2. **No look-ahead** — data layer's `slice(end=asof)` clips to `asof - embargo`; strategy cannot request bars past `asof`.
3. **Exam access log** — writes to `exam_access.log` on every read of exam split; manual review required before any exam-based promotion.
4. **Sealed results** — `results/{run_id}/SEALED` sentinel + parquet files chmod 444 after write; delete requires manual `rm -rf` (deliberate friction).
5. **Engine hash pinning** — every result manifest records git commit of engine; rerunning old result requires checkout of same commit or explicit `--allow-hash-drift` (logged).
6. **Speed limit enforced in sizing** — risk-adj cost > 0.08 → sizing wrapper refuses to size, logs `speed_limit_violation`, trial marked skipped.
7. **Account cap enforced in sizing** — `target_risk > 30%` → hard reject at config load, engine won't start.
8. **HTB registry** — pre-computed per-day HTB list. Short signal on HTB name → skip trade, log.
9. **Modern-era boundary** — data layer rejects bars pre-2007-01-01.
10. **DSR-required-for-pass** — `verdict.pass = False` if `K_effective < 5`. Prevents "single trial genius" false positive.

### Explicit anti-guardrails

Things v2 does **not** protect against:

- **Bad economic mechanism.** Engine can't judge if `mechanism` docstring is nonsense. Manual review at promotion.
- **Regime absence.** If 2007–present has never seen a regime that would kill the strategy (e.g., 1970s stagflation), we won't warn. Documented limitation.
- **Broker outage / API drift.** Deferred to executor build.
- **Human override.** Owner can force `verdict.pass = True`; logged with `manual_override` flag. Discipline, not lock.

### Recovery playbook

| Situation | Action |
|---|---|
| Backtest crashed mid-run | Restart with same `run_id` → engine detects partial `trials.parquet`, resumes remaining `trial_id`s |
| Result set corrupted | Delete result dir (deliberate friction), rerun with same params → new `run_id` |
| Engine bug found post-promotion | Retract passed strategies (mark `retracted` in `verdict.json`), no silent replay — force manual rerun |
| Deep research adds new gate criterion | Bump engine version, mark old results `stale_gate`, require rerun for promotion — old paper trades keep running (grandfather) |

## Testing

### Test pyramid

```
End-to-end (5)      full flow: ingest → gate → verdict
Integration (~30)   module pairs
Unit (~150)         per-function, per-formula
```

### Unit (~150)

**Data Layer** — reject pre-2007 query; reject unsealed exam access; purge/embargo math at fold boundaries; regime tag correctness on known dates (2008-10-09 bear, 2017-01-03 bull-calm); checksum mismatch fatal; split-adjust matches yfinance on known splits (AAPL 2020 4:1).

**Strategy Interface** — plugin missing `mechanism` rejected; missing `display_name` rejected; forecast outside [−20, +20] clipped + logged; forecast requesting future bars → assertion fires.

**Sizing Wrapper** — Carver Ch.5 worked example golden test; half-Kelly = SR÷2; IDM Table 43 lookup on 5 known cells; speed-limit trigger at cost = 0.081; account-cap 30% hard reject at config load; LIFO de-risk order.

**Execution Sim** — market order full-spread golden PnL; limit fill at midprice inside spread; no-fill outside spread; slippage monotonic in order size; HTB short skipped; borrow fee per day; halt day → next open; gap > 5% flagged + filled.

**Gate** — DSR formula vs Lopez Ch.8 worked example (γ = 0.5772, N=1000 → E[max SR] ≈ 3.26); FWER for known K; ONC clustering on synthetic block-diagonal correlation → K_eff = block count; per-regime Sharpe on synthetic labeled returns; verdict SHELF on negative regime with sample ≥20%; verdict `insufficient_trials` at K < 5.

### Integration (~30)

- Strategy → sizing (Carver formula end-to-end): forecast=+20, σ=20%, equity=$100k, target=12% → notional=$60k
- Sizing → execution: sized order → fill → PnL vector matches expected
- Execution → gate: synthetic P&L → DSR + FWER + verdict deterministic
- CPCV fold generator: 10 folds, k=2 → 45 combos, no purge/embargo violation
- Regime tagging + data slicing: bear regime slice returns only bear-labeled bars
- Same-code-path check: `execution_sim` vs live-executor stub on same historical bars → byte-identical fill vector
- v1 replication: v2 engine on v1's momentum rotation config → same P&L vector as v1

### End-to-end (~5)

Each uses a synthetic strategy with known ground truth.

1. **Coin-flip strategy** (random ±10 forecast, seeded) → verdict = SHELF (DSR ≈ 0)
2. **Perfect oracle** (peeks at t+1, ±20 forecast) → verdict = PASS. Only PASS case that fires without external help. Asserted.
3. **Buy-and-hold SPY replica** → v2 Sharpe matches v1 within 1e-6
4. **Regime-conditional oracle** (only works in bull) → verdict = SHELF via regime kill rule
5. **CPCV leak test** — strategy that peeks past embargo → engine assertion fires

### Property tests

- No-look-ahead: for any random slice/strategy pairing, `strategy.forecast(bars, asof)` output ⊥ `bars[asof+1:]`
- Determinism: same inputs + engine hash → byte-identical outputs
- Sealed idempotence: re-run same `run_id` → refuses to overwrite

### Out of scope for testing

- Live broker connectivity (deferred to executor phase)
- Options Greeks (deferred to options leg)
- Minute-bar aggregation (deferred to day-trading leg)
- Dashboard rendering (already covered by v1 AppTest suite; extend for verdict panel)

### CI gate

- All unit + integration + e2e green before merge
- v1 replication test = red build
- Coverage floor: 85% on `src/engine_v2/`
- Golden-value tests use small fixture data checked into repo (< 5 MB)

## Sequencing (rough)

Not a plan — that comes next via writing-plans skill.

- **Week 1** — Data layer (Schwab ingest, modern-era boundary, regime tagging, sealed splits, playground/exam)
- **Week 2** — Strategy interface + Sizing wrapper (Carver stack) + v1 replication test
- **Week 3** — Execution sim (real costs, shorting, HTB, halt/gap) + same-code-path CI
- **Week 4** — Validation gate (WF+CPCV, DSR, FWER, ONC, per-regime, verdict) + dashboard extension

Deep-research candidates land during weeks 3–4. First real strategy plugin follows immediately after v2 ships.

## References

- Carver, *Leveraged Trading* — `05 Library/Leveraged Trading (Carver)/00 Leveraged Trading — Index.md`
- Chan, *Machine Trading* — `05 Library/Machine Trading (Chan)/00 Machine Trading — Index.md`
- Lopez de Prado, *Machine Learning for Asset Managers* — `05 Library/Machine Learning for Asset Managers (Lopez de Prado)/00 Machine Learning for Asset Managers — Index.md`
- Decision: mission pivot — `03 Decisions/2026-07-06 — Pivot from investing to trading (swing-day, short-options).md`
- Decision: long backtests + decay detection — `03 Decisions/2026-07-05 — Long backtests + decay detection (not short windows).md`
- v1 spec — `docs/superpowers/specs/2026-07-05-research-engine-design.md`
