# Dashboard Honesty & Benchmark — Design Spec

**Date:** 2026-07-06
**Status:** Approved by owner (session 2026-07-06, round 3)
**Depends on:** `2026-07-05-dashboard-design.md` (v1), `2026-07-06-dashboard-polish-design.md` (round 2, merged)

## Goal

Fix the dashboard's color semantics so they agree with its own multiple-testing
warning, put the SPY benchmark on every page that shows performance, and remove
the remaining readability friction (cropped table, snake_case headers, truncated
labels, indistinguishable compare curves).

## Problem (audit findings, 2026-07-06)

1. **Color honesty broken.** Leaderboard shades Sharpe relative to batch
   min/max: 0.68 renders deep green while the luck line is 0.79 — every run in
   the batch is statistically noise, yet the table reads as "winners here".
   Plateau heatmap is worse: RdYlGn stretched over a 0.48–0.59 spread implies
   robust structure inside what is a flat noise surface.
2. **No benchmark anywhere.** The screening verdict was "everything
   underperformed buy-hold SPY", but SPY appears on zero charts and in zero
   tables.
3. **Leaderboard friction.** 17 columns crop off-screen; only ~10 of 31 rows
   visible; snake_case headers; row-select → run detail linkage exists but is
   invisible (muted caption).
4. **Compare curves indistinguishable.** Same-family runs share one color,
   differ only by dash — unreadable on dense daily lines. Picker chips truncate
   so the picked runs can't be identified.
5. **Small stuff.** Batch selector shows truncated raw filename; plateau with
   one swept param renders a giant one-row heatmap; generic "value" axis labels.

## Owner decisions

- Approach A approved: single round, pure presentation layer. No engine
  changes, no screen rerun.
- Benchmark computed dashboard-side from the same frozen playground parquet.
  Accepted as temporary duplication: dies when the engine grows real benchmark
  rows (already in the iteration queue).
- Luck banner stays exactly as is (round-2 decision unchanged).

## Non-goals

- No engine/loader/recompute behavior changes (one exception below: pure
  additive extraction of `luck_sharpe`).
- No new pages, no dark mode, no verdict buttons.
- No 60/40 benchmark (SPY buy-hold only — YAGNI until engine-side rows exist).

## Design

### 1. Color honesty

**`src/batch/runner.py` (additive refactor only):** extract
`luck_sharpe(n_runs: int, years: float) -> float` returning
`sqrt(2·ln(max(n,2))/years)`; `luck_warning` calls it. No behavior change.

**`style.py`:** `style_metrics(df, luck_sharpe: float | None = None)`.

- Sharpe column, when `luck_sharpe` given: absolute anchors instead of batch
  min/max. Below luck line → no green; faint gray `rgba(100,116,139,·)` whose
  alpha grows as the value falls further below the line. From luck line to 1.0
  → pale-to-mid green ramp. Above 1.0 → strong green (alpha capped as today).
- `luck_sharpe=None` → current relative behavior unchanged (back-compat
  default). Both leaderboard **and compare** tables pass the batch's
  luck threshold — same honesty rule everywhere.
- CAGR / max_dd keep relative shading; the benchmark row (§3) joins the table
  and thereby sets the visible bar.

**`views/plateau.py`:** heatmap color scale pinned to absolute range:
`zmin=0`, `zmax=max(1.0, data_max)`, `zmid=luck_sharpe` (RdYlGn — red below
luck, green only past it). Only applied when metric is `sharpe`; other metrics
keep data-driven range. Colorbar gets a horizontal line + annotation
"luck line ≈ X.XX".

### 2. Leaderboard readability — `views/leaderboard.py`

- Friendly headers via `st.column_config` label=: Strategy, Sharpe, CAGR,
  Max DD, Trades, Sample, Turnover/yr, Exposure, Best yr, Worst yr,
  Top-2 share. Raw `label` column stays last, header "(technical)".
- Merge `positive_years` + `total_years` into one string column "Years up" =
  `"9 of 11"`, replacing both. (Display-frame only; CSV untouched.)
- `st.dataframe(height=...)` sized to row count (`35 * (len+1)` px, capped
  ~1200) — all 31 rows visible, no inner scroll.
- On row select: replace muted caption with `st.page_link` to the run page,
  label `"Open run detail → <friendly name>"`.

### 3. Benchmark — new `dashboard/benchmark.py`

```
spy_equity(playground: pd.DataFrame) -> pd.Series | None
    # SPY close series from the parquet, normalized to $100k start;
    # None if SPY absent.
benchmark_row(equity: pd.Series) -> dict
    # engine's summarize() + fixed label/name/strategy fields:
    # label="benchmark_spy", name="benchmark",
    # friendly "S&P 500 buy & hold — benchmark"
```

- Cached via `st.cache_data` in `shared.py` (same pattern as `run_result`).
- **Leaderboard:** benchmark row inserted at top of display frame, included in
  the Styler so it participates in CAGR/max_dd shading; row background tinted
  `#F1F5F9` via Styler `.apply` on the label match. Excluded from luck-line
  logic (it's one run, not part of the multiple-testing pool).
- **Run detail:** SPY line (gray `MUTED`, width 1.5) overlaid on the equity
  chart, legend on, normalized to same $100k start.
- **Compare:** SPY always drawn as gray dashed reference (not counted toward
  the 2–4 picks).
- SPY missing → every benchmark feature silently skipped except one
  `st.info("SPY not in dataset — benchmark hidden")` on the leaderboard.

### 4. Compare distinguishability — `views/compare.py`

- Within-family color separation: runs of the same family get
  lightness-stepped variants of the family color (step ±12% lightness per
  additional run, via a small `style.shade_family(color, i, n)` helper). Dash
  cycle retained as secondary cue.
- Under the multiselect: `st.caption` listing full friendly names of current
  picks (chips truncate; the caption doesn't).

### 5. Misc

- `naming.py`: `batch_label(filename) -> str` —
  `leaderboard_20260705-224738_42e964f.csv` → `"Jul 5 2026, 22:47 · 42e964f"`;
  unparseable → filename unchanged. Sidebar selectbox uses it via
  `format_func`.
- `views/plateau.py`: when the pivot has a single row `"(all)"` → horizontal
  bar/line chart (metric vs param values) instead of a one-row heatmap.
- Axis labels: equity y → `"$"`, drawdown y → `"% below peak"`, compare y →
  `"growth of $1"`.

## Error handling

- `spy_equity` returns None on missing ticker/empty frame; all call sites
  guard.
- `batch_label` never raises; regex miss → raw name.
- `style_metrics` with `luck_sharpe=None` → today's behavior (back-compat for
  compare view).
- Plateau 1-D branch handles single param value (one bar) without crashing.

## Testing

- `tests/test_benchmark.py`: spy_equity normalization (starts at 100k),
  None on missing SPY, benchmark_row fields present.
- `tests/test_naming.py`: extend for `batch_label` (happy path + garbage in).
- `tests/test_style.py`: sharpe shader — below-luck value gets no green,
  above-1 gets max alpha, `luck_sharpe=None` falls back to relative;
  `shade_family` returns n distinct colors.
- `src/batch` test: `luck_sharpe` extraction — `luck_warning` string unchanged
  for known inputs.
- Visual pass: run app against the real batch CSV, screenshot all four pages.

## Build order

1. `luck_sharpe` extraction + tests (touches src, do first, smallest).
2. `style.py` absolute sharpe shader + `shade_family` + tests.
3. `benchmark.py` + shared caching + tests.
4. Leaderboard upgrade (headers, height, years-up merge, benchmark row,
   page_link).
5. Run detail + compare benchmark overlays; compare shades + picks caption.
6. Plateau absolute scale + 1-D branch; batch_label + sidebar; axis labels.
