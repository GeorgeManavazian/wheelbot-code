# Design: Intraday Data Layer — local adapter first (SPY/QQQ 1-min)

- **Date:** 2026-07-11
- **Status:** design — awaiting review
- **Affects:** `src/engine_v2/data`, dashboard Run page, a new build script, fixtures
- **Builds on:** the workbench (`DataSource` seam, `run_simple`, frequency-aware metrics) shipped 2026-07-10

## Problem

The workbench runs on daily bars only. The mission (swing/day) needs intraday. We discovered clean 1-minute ETF bars already on disk — no purchase needed to start. Wire them in behind the existing `DataSource` seam so intraday backtests run this session, and keep a live pull (ThetaData/Databento, broader universe) as a later phase.

## What exists (verified 2026-07-11)

In `~/Documents/Trading data/`:
- **SPY** `SPY_1m_2021-07-01_2026-07-03_ARCX.parquet` — 1,128,145 rows; **QQQ** `QQQ_1m_2021-07-01_2026-07-03_ARCX.parquet` — 1,106,879 rows.
- 1-minute OHLCV (`open/high/low/close/volume`, lowercase), DatetimeIndex named `ts_event`, **tz-aware America/New_York**, includes pre/post market (04:00–20:00).
- Filtered to regular hours (09:30–15:59): **exactly 390 bars/day median, 0 NaNs, 0 duplicate timestamps**, ~1,255 trading days each.
- Common span **2021-07-01 → 2026-07-02**. Looks like **Databento** output (`ts_event` + `ARCX`/`EQUS` codes), not ThetaData.
- **Intraday coverage is SPY + QQQ only** — no other ETFs at minute resolution locally.

Also present (out of scope here): SPX options-minute archive (~23,715 files) and the repo's `data/raw/` daily per-ticker parquets.

## Design

Mirror the existing daily pattern (source → build script → cache → `DataSource` → tiny committed fixture for tests). Three new pieces, one wiring change.

### 1. `scripts/build_intraday_cache.py`

Materializes a clean, portable, repo-local cache from the external source parquets.

- Input dir configurable: `--source-dir` (default `~/Documents/Trading data`), so the machine-specific path is not hard-coded into library code.
- For each of SPY, QQQ: read the `*_1m_*_ARCX.parquet` full-span file.
- **Session filter:** keep RTH 09:30–15:59 ET by default; `--include-extended` keeps 04:00–20:00.
- **Normalize:** rename `open/high/low/close/volume` → `Open/High/Low/Close/Volume` (the sim reads `bars[tkr]["Close"]`); convert the index to **tz-naive ET** (`tz_convert("America/New_York").tz_localize(None)`) for consistency with the daily side.
- **Assemble** one DataFrame with MultiIndex columns `(ticker, field)` across [SPY, QQQ], aligned on the common index (outer join, so a missing bar in one ticker is NaN rather than dropped).
- Write to gitignored `data/intraday/intraday_1m.parquet` (full ~5y cache, ~40 MB — NOT committed).
- Also write a small committed fixture `fixtures/intraday_1m_small.parquet` — SPY+QQQ, RTH, the first **5 trading days** (~3,900 rows) — for tests, mirroring `bars_2007_2010_small.parquet`.
- Add `data/intraday/` to `.gitignore`.

### 2. `IntradaySource` (in `src/engine_v2/data/source.py`)

- `INTRADAY_PATH = "data/intraday/intraday_1m.parquet"`, `INTRADAY_FIXTURE_PATH = "fixtures/intraday_1m_small.parquet"`.
- Reuse the existing `ParquetSource` — it already implements `load`/`available_tickers`/`date_range` against a MultiIndex `(ticker, field)` parquet. Add `intraday_source()` returning `ParquetSource(INTRADAY_PATH)` (parallel to `default_source()`).
- `load_bars` (loader) enforces `MODERN_ERA_START = 2007`; 2021 intraday passes. Its `df.loc[start:end]` slice works on the tz-naive minute index (date-only bounds include whole days).

### 3. Dashboard Run page — data-source selector

- Add a radio/selectbox `key="data_source"`: **"Daily (20-ETF universe)"** (default, `default_source()`) vs **"Intraday 1-min (SPY/QQQ)"** (`intraday_source()`).
- The rest of the page is unchanged: tickers/date pickers repopulate from the selected source's `available_tickers()`/`date_range()`; `run_simple` and the Result rendering already work because metrics are frequency-aware (1-min RTH → `periods_per_year ≈ 98,280`, derived from the 60-s median gap).
- Guard: if the intraday cache file is absent (build script not yet run), show a friendly message with the exact build command instead of crashing.

### 4. Proof (data layer, not a new strategy)

Per the owner's choice ("just the data layer first"): prove the pipeline end-to-end by running an **existing** plugin through `run_simple` on the intraday fixture and asserting a `Result` with a moving equity curve. Parameters designed for daily bars become "N-minute" intraday and are economically meaningless — this is a **wiring smoke test, not a strategy evaluation**. A real intraday strategy (opening-range breakout / gap-fade) is deliberately a later task.

## Data flow

```
~/Documents/Trading data/{SPY,QQQ}_1m_*_ARCX.parquet   (Databento, external, gitignored location)
   -> build_intraday_cache.py  (RTH filter, capitalize OHLCV, tz-naive ET, MultiIndex)
   -> data/intraday/intraday_1m.parquet          (gitignored cache)   + fixtures/intraday_1m_small.parquet (committed, 5 days)
   -> IntradaySource / ParquetSource(INTRADAY_PATH)
   -> run_simple (unchanged; periods_per_year auto-derived) -> Result
   -> Run page (Daily | Intraday selector)
```

## Error handling

- Source parquet missing when building → clear error naming the expected file + `--source-dir`.
- Cache missing when the Run page selects Intraday → friendly "run `python scripts/build_intraday_cache.py` first" message, no crash.
- Half-days (min ~333–360 bars) → fewer bars is fine; no special handling.
- A ticker absent from the selected source → existing `KeyError`-to-message path.

## Testing

- `build_intraday_cache` (unit, against a tiny synthetic or the committed fixture path): RTH filter keeps only 09:30–15:59; columns are capitalized; index is tz-naive; output is MultiIndex `(ticker, field)`.
- `IntradaySource`/`intraday_source`: `available_tickers() == {"SPY","QQQ"}`; `date_range()` matches the fixture; `load(["SPY"], ...)` slices correctly.
- Frequency: `run_simple` on the intraday fixture yields `periods_per_year` in the ~90k–100k range (not 252) and a `Result` whose equity moves.
- Metrics sanity: annualized Sharpe on the intraday fixture is finite (not inf/NaN).
- Dashboard: `AppTest` — selecting the Intraday source and clicking Run against the committed fixture renders ≥1 metric with no exception. (Test points the source at the fixture path, so it never needs the full cache.)

## Non-goals

- No live ThetaData/Databento API pull (separate later phase for broader-universe intraday).
- No new intraday strategy logic (proof uses an existing plugin).
- No intraday universe beyond SPY/QQQ (that's all that exists locally).
- No options data (SPX archive is a separate, later track — the options endgame).
- Extended-hours strategies (cache defaults to RTH; `--include-extended` exists but is not wired into the UI yet).

## Open choices (defaulted here; flag to change)

- **RTH-only** default (390 bars/day). Rationale: cleaner, matches canonical intraday strategies; extended hours available via build flag.
- **tz-naive ET** index. Rationale: consistency with the daily side; the sim only needs monotonic ordering.
- Canonical source = the `*_ARCX.parquet` full-span files (2021–2026 complete in one file per ticker).
