# Design: Wheel sub-project 1 — SPY options data pull + core primitives

- **Date:** 2026-07-11 (rewritten — SPY-first, replaces the earlier SPX-scaffold draft)
- **Status:** design — awaiting review
- **Part of:** the Wheel-on-SPY options backtester (endgame). Sub-project 1 of ~5.
- **Scope:** pull real SPY option chains from ThetaData → normalize → a committed fixture, plus the pure selection/marking primitives. No Wheel cycle, no P&L loop (sub-project 2).

## Why SPY-first (not SPX-scaffold)

The earlier draft proposed building against local SPX daily-chain files. Rejected: those files are a dead post-processed format from a defunct tool, and SPX is cash-settled (no assignment). SPY is the real target, and **ThetaData access is now proven working** (2026-07-11) — real SPY chains with greeks flow from the local terminal. So we build directly on real SPY data; nothing is throwaway.

## What is proven (see `docs/thetadata-v3-access.md`)

Local ThetaData terminal serves REST at `http://127.0.0.1:25503`. Confirmed with live SPY data back to 2012:
- `/v3/option/list/expirations?symbol=SPY` → all expirations (CSV).
- **`/v3/option/history/greeks/eod?symbol=SPY&expiration=<ISO>&start_date=<ISO>&end_date=<ISO>&strike_range=<n>`** → whole-chain daily EOD **with greeks**. Columns include `strike, right, close, bid, ask, delta, implied_vol, underlying_price` and a report `timestamp`. This single endpoint gives everything strike-selection (delta) and marking (bid/ask) need.
- Responses are **CSV**; dates are **ISO** `YYYY-MM-DD`; auth is implicit (terminal holds the key from gitignored `.env`).

## Data model

### `OptionsChain` (normalized tidy frame)
One row per (as-of `date`, contract). Columns:
`date` (tz-naive, the EOD report date), `expiry` (date), `dte` (int = expiry−date days), `strike` (float $), `right` ("P"|"C"), `bid`, `ask`, `mid` (=(bid+ask)/2), `close`, `delta`, `iv`, `underlying`.
Normalization from the raw greeks/eod CSV: `right` "CALL"/"PUT" → "C"/"P"; `date` = date part of the report `timestamp`; `implied_vol`→`iv`; `underlying_price`→`underlying`; strike coerced to float dollars.

### `Contract` (dataclass)
`root: str, expiry: date, strike: float, right: str` — immutable option identity. `right ∈ {"P","C"}`.

### `Mark` (dataclass)
`bid: float, ask: float, mid: float`.

## Components / where code lives

New package `src/engine_v2/options/` (sits beside the equity engine; imports none of it, no gate):

**`theta_client.py`** — thin REST client for the local terminal.
- `ThetaClient(base_url="http://127.0.0.1:25503", timeout=30)`.
- `get_csv(path, **params) -> pd.DataFrame` — GET, raise on non-200 (surfacing the CSV/HTML error body), parse CSV.
- Typed helpers: `list_expirations(symbol) -> list[date]`; `chain_greeks_eod(symbol, expiration, start, end, strike_range) -> pd.DataFrame` (raw).
- `is_up() -> bool` — quick liveness check (used to skip live tests when the terminal is down).
- Respects the 4-concurrent limit: pulls are sequential in sub-project 1 (no parallelism yet).

**`chain.py`** — `normalize_greeks_eod(raw) -> OptionsChain` (raw CSV → tidy frame), `Contract`, `Mark`.

**`select.py`** — the pure primitives:
- `select_strike_by_delta(chain, date, right, target_delta, dte_min, dte_max) -> Contract | None` — among rows on `date` with `right` and `dte ∈ [dte_min, dte_max]`, pick nearest `abs(delta)` to `target_delta`; ties → further-OTM; `None` if none in range.
- `option_mark(chain, date, contract) -> Mark | None` — bid/ask/mid for `contract` on `date`; `None` if absent (no fabrication).
- `intrinsic_value(right, strike, underlying) -> float` — put `max(strike−underlying,0)`, call `max(underlying−strike,0)`.
- `expiry_underlying(chain, contract) -> float | None` — underlying on the contract's expiry date.

**`scripts/pull_spy_options.py`** — CLI puller.
- Args: `--symbol SPY`, `--start`, `--end` (date window to cover), `--strike-range N`, `--dte-max` (to bound how far before expiry to pull), `--out`.
- Logic: list expirations; for each expiration whose window overlaps [start,end], pull `chain_greeks_eod` over `[expiration − dte_max days, expiration]` with `strike_range`; normalize; concat; write a gitignored cache parquet `data/options/spy_greeks_eod.parquet`.
- Also writes the small **committed** fixture `fixtures/spy_options_small.parquet` — ~2 expirations (a couple months, near-money strikes), a few thousand rows — for tests.
- `data/options/` added to `.gitignore`.

## Data flow

```
ThetaData terminal (127.0.0.1:25503, holds API key)
  -> ThetaClient.chain_greeks_eod(SPY, exp, start, end, strike_range)   (CSV)
  -> normalize_greeks_eod  -> OptionsChain tidy frame
  -> pull_spy_options.py  -> data/options/spy_greeks_eod.parquet (gitignored cache)
                          -> fixtures/spy_options_small.parquet (committed, ~2 expiries)
  -> select_strike_by_delta / option_mark / intrinsic_value / expiry_underlying
       (pure primitives — consumed by the Wheel engine in sub-project 2)
```

## Error handling

- Terminal down (`ThetaClient.is_up()` false) → puller exits with a clear message: start it with the command in `docs/thetadata-v3-access.md`.
- Non-200 / HTML error body → `get_csv` raises with the status + first line of the body (e.g. the 410 deprecation or 404).
- Empty chain for an expiration/date → skipped with a logged count (holidays, no data).
- `select_strike_by_delta` no contract in window → `None` (valid "no trade").
- `option_mark` contract absent on a date → `None` (caller decides gap handling).
- Rows with bid/ask ≤ 0 or NaN delta → dropped at normalize with a logged count.

## Testing

- `normalize_greeks_eod` (against a small recorded CSV fixture, no network): right→P/C, date parsing, iv/underlying rename, mid computation, bad-row drop.
- `select_strike_by_delta` (on the committed parquet fixture): a 0.30-delta put request returns |delta| nearest 0.30 within the DTE window; empty window → `None`; tie → further-OTM.
- `option_mark`: held contract returns bid/ask/mid on a later date; absent → `None`.
- `intrinsic_value`: put/call incl. zero floor.
- `expiry_underlying`: underlying on expiry date; `None` when absent.
- Golden check: pin one selected strike + its delta + mark to fixed values from the fixture.
- **Live smoke** (`ThetaClient`): `@skipif` when `is_up()` is false — pull one small SPY chain, assert columns + a plausible put delta in [−1, 0]. Proves the client end-to-end locally; skips in CI.

## Non-goals (later sub-projects)

- No Wheel state machine, assignment, covered calls, rolling, take-profit, collateral, or P&L (sub-project 2).
- No intraday option marks (sub-project 4 — uses `option/history/ohlc`/`greeks/all` with `interval`).
- No full multi-year bulk pull as a deliverable — the puller *supports* it (wide `--start/--end`), but sub-project 1 only commits a small fixture; scaling the pull is a later action.
- No reporting, no dashboard wiring.

## Open choices (defaulted; flag to change)

- **Delta from the chain's provided `delta`** column (honest to the data), not recomputed.
- **`strike_range` band** default wide enough to cover ~5-delta…ATM on both sides (so 16–30-delta selection always has candidates); exact N tuned when the puller runs.
- **EOD only** here; intraday deferred to sub-project 4 (your take-profit-timing insight).
- Fixture underlying = **real SPY** (numbers in tests are SPY, assignable — correct for the Wheel).
