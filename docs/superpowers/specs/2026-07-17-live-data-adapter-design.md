# Live data adapter (sub-project A of the paper-trading bot) — design

**Date:** 2026-07-17
**Project:** Live paper-trading bot — `code/etf-bot`, sub-project A of 4 (A data adapter → B paper-execution/state → C daily orchestration → D live dashboard)
**Status:** design, pending implementation plan
**Depends on / consumes:** the Schwab client (`scripts/schwab/schwab_client.py`, py3.12 `.venv-live`), the engine's regime + wheel schemas (`regime/state.py`, `options/select.py`).

## Problem

The paper-trading bot needs live market data in the exact shapes the existing engine consumes. Schwab's client works (`price_history` + bounded `option_chain` both return 200), but returns nested JSON. This sub-project builds the **adapter**: Schwab JSON → engine-native `pd.Series` (daily closes) and `pd.DataFrame` (option chain). Stateless, testable, foundational — everything downstream (weather signal, put selection, paper fills) reads these shapes.

## Non-goals

- Order placement (data-only; the client has no order code by construction).
- Bar caching (owner dropped it — pull fresh each run; caching is a later drop-in if daily runs feel slow).
- The decision logic, paper fills, state, orchestration, or dashboard — those are sub-projects B/C/D.
- Deciding *which* tickers to pull chains for (regime-gating) — that orchestration lives in C; A provides the per-ticker `chain_frame` function.
- The final tuned universe / N — A ships a starter universe list; tuning is later.

## Architecture

New top-level package **`live/`**, run on the `.venv-live` (py3.12) environment. It imports both the Schwab client and the engine (both import cleanly on 3.12, verified). The **py3.9 backtest test-suite never imports `live/`** — the two environments stay separate; `live/` is 3.12-only.

Files this sub-project ships:
- `live/__init__.py`
- `live/universe.py` — `UNIVERSE: list[str]`, ~150–200 curated liquid optionable tickers (mega/large-cap stocks + liquid ETFs), owner-editable. No reserved-ticker constraint (live forward data is inherently out-of-sample).
- `live/data.py` — the adapter (two functions + a throttle helper).
- `live/fixtures/price_history_gdx.json`, `live/fixtures/option_chain_gdx_puts.json` — real saved Schwab responses (already pulled) for offline mapping tests.

## The adapter (`live/data.py`)

### `daily_closes(client, ticker) -> pd.Series`

From Schwab `client.get_price_history_every_day(ticker)`. Response JSON: `{"candles": [{"open","high","low","close","volume","datetime"}, ...]}` where `datetime` is epoch-ms. Produce a `pd.Series` of `close` indexed by normalized date (`pd.to_datetime(datetime, unit="ms").normalize()`), sorted, de-duplicated (keep last per date). This is exactly what `regime_series(closes)` consumes.

### `chain_frame(client, ticker, target_dte, strike_count=12) -> pd.DataFrame`

From Schwab `client.get_option_chain(ticker, contract_type=PUT, strike_count=..., from_date=today, to_date=today+window)`, where the window brackets `target_dte` (e.g. `today .. today + target_dte + 20` days) so the target expiry is covered. **Bounded pull only** — the full chain 502s on payload size, and the wheel only needs puts near target delta/DTE.

Response JSON has `underlyingPrice` (top-level) and `putExpDateMap`, nested `{"YYYY-MM-DD:DTE": {"STRIKE": [contract]}}`. Each contract carries `strikePrice, bid, ask, mark, delta, daysToExpiration, expirationDate, putCall`. Flatten to one row per contract with the **exact engine columns** (`select_contract` / `option_mark` read these):

| engine column | source |
|---|---|
| `date` | today (the observation date), normalized |
| `expiry` | the expiry key's date part (`"2026-07-17:0"` → `2026-07-17`), normalized |
| `strike` | `strikePrice` (float) |
| `right` | `"P"` (from `putCall` == PUT) |
| `dte` | `daysToExpiration` (int) |
| `delta` | `delta` (float; Schwab puts are negative — engine uses `.abs()`, so keep the sign as-is) |
| `bid` | `bid` (float) |
| `ask` | `ask` (float) |
| `mid` | `mark` (float — Schwab's mark price) |
| `underlying` | top-level `underlyingPrice` (float) |

Skip contracts with non-numeric/`NaN` delta or non-positive bid/ask (illiquid placeholders) — mirrors the engine's own filtering discipline.

### `throttle` helper

A minimal rate guard: ~120 requests/min (Schwab's cap), plus a small retry (2 tries) on transient `429`/`502`. Used by any batch caller; A itself exposes single-ticker functions, so the throttle is a decorator/util the daily runner (C) wraps around batch loops.

## Data flow

```
Schwab client (py3.12)
   ├── price_history(ticker) ──▶ daily_closes()  ──▶ pd.Series[date→close]  ──▶ regime_series()
   └── option_chain(ticker,   ──▶ chain_frame()   ──▶ pd.DataFrame[date,expiry,strike,
        bounded puts)                                   right,dte,delta,bid,ask,mid,underlying] ──▶ select_contract/option_mark
```

## Error handling

- Non-200 from Schwab → raise a clear error naming the ticker + status (the caller/runner decides skip-vs-abort; A doesn't swallow).
- Empty `candles` or empty `putExpDateMap` → return an empty Series / empty DataFrame with the right columns (no crash; caller sees "no data for ticker").
- `429`/`502` → the throttle helper's bounded retry; still failing → raise.

## Testing

Mapping correctness is the real risk (a swapped field, a delta sign flip). Tests run **offline against the saved fixtures** — no live API in the suite, reproducible.

1. **`daily_closes` mapping** (`live/fixtures/price_history_gdx.json`): asserts a datetime-indexed, sorted, de-duplicated close Series; length matches the fixture's candle count; a spot-checked known (date, close) pair matches.
2. **`chain_frame` mapping** (`live/fixtures/option_chain_gdx_puts.json`): asserts the exact engine columns exist; `right == "P"`; `strike`/`bid`/`ask`/`mid`/`delta`/`dte`/`underlying` match the fixture for a spot-checked contract; `underlying == 71.32`; `bid <= ask`; delta is negative for puts.
3. **Empty-input handling**: an empty-candles / empty-chain JSON yields an empty Series / correctly-columned empty DataFrame, no exception.
4. **Live smoke script** (`live/smoke_pull.py`, not a pytest): hits Schwab, prints a real mapped GDX chain + close series head — eyeball end-to-end. Run manually with `.venv-live`.

Tests run under `.venv-live` (they import `live/`, which is 3.12-only): `PYTHONPATH=. .venv-live/bin/python -m pytest live/ -v`. Note: the main 3.9 suite does not and must not collect `live/` (it imports schwab). The plan will confirm pytest config excludes `live/` from the 3.9 run.

## Files touched

- `live/__init__.py`, `live/universe.py`, `live/data.py`, `live/smoke_pull.py` — new.
- `live/fixtures/*.json` — already pulled, committed as test fixtures (market data only, no secrets).
- `live/tests/test_data_mapping.py` — new.
- `.gitignore` — `data/live/` (future bar/state dirs) if not already ignored.

## Open items

None blocking. The starter universe list is a first draft to be tuned later; N and the final universe are sub-project B/C concerns.
