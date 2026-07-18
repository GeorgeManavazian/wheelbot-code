# Live paper runner + state store (sub-project B2) — design

**Date:** 2026-07-17
**Project:** Live paper-trading bot — sub-project B2 (of B). B1 (step extraction) is DONE on main.
**Status:** design, pending implementation plan. **Autonomous build** (owner away, authorized; see [[live-paper-bot]] memory).
**Depends on / consumes:** `step_one_day`/`PortfolioState`/`Market` (B1, `src/engine_v2/options/portfolio.py`, `market.py`), the live adapter (`live/data.py`: `daily_closes`/`chain_frame`), the Schwab client (`scripts/schwab/schwab_client.py`), `regime_series`/`is_good_renting_weather`.

## Problem

B1 gave us a shared `step_one_day(state, market, day, cfg, *, selector, n_slots)`. B2 makes it run **live, one day at a time, statefully**:
1. **`LiveMarket`** — the `Market` interface backed by *today's* Schwab data + carried prices (so the same `step` that ran the backtest now runs live).
2. **State persistence** — serialize/deserialize `PortfolioState` to JSON so the bot remembers positions/cash across days.
3. **The daily runner** — load state → build `LiveMarket` for today → `step_one_day` once → append trades → save state.

Data-only, simulated fills against real Schwab bid/ask, no order code. Runs on `.venv-live` (py3.12).

## Non-goals

- Account-size affordability filter + N re-tuning — sub-project B3. (Note: the engine's existing `n = budget // (strike*100)` sizing *already* makes a small account skip unaffordable puts — `n=0` → not entered — so B2 is usable at any account size; B3 refines efficiency + edge cases.)
- P&L / daily snapshot recording for the dashboard — sub-project B4. (B2 persists state + a trade log; rich snapshots are B4.)
- The dashboard — sub-project D.
- Scheduling (launchd/cron) — a thin wrapper added in B2's runner section is a manual `run_daily.py`; automated scheduling is a later concern.
- Any change to `step_one_day` or the batch engine — B1 is frozen.

## Architecture

Three new files in `live/` (py3.12):

### 1. `live/state.py` — persistence

`PortfolioState` ⇄ JSON. Positions contain `Contract` frozen dataclasses (fields `root, expiry, strike, right`) nested in a `short` dict; JSON needs explicit (de)serialization.

- `state_to_dict(state) -> dict` — serialize cash, campaign, days_flat, days_shares_uncovered, `prev_d` (ISO date or null), and each position (ticker, shares, phase, basis, premium, campaign, last_spot, and `short` → `{contract: {root, expiry ISO, strike, right}, contracts, credit, last_mid}` or null).
- `state_from_dict(d) -> PortfolioState` — inverse; rebuild `Contract(root, Timestamp(expiry), strike, right)` and `pd.Timestamp(prev_d)`.
- `save_state(state, path)` — atomic write (temp file + rename) so a crash can't corrupt state.
- `load_state(path) -> PortfolioState | None` — `None` when the file is absent (first run → the runner starts fresh).

### 2. `live/market_live.py` — `LiveMarket`

Implements the `Market` interface over live data. **Efficient by construction** — the routing loop in `step` calls `chain(tk)` for every universe ticker, so `LiveMarket` avoids pulling 128 chains by only pulling the ones that matter:

Construction `LiveMarket(client, universe, held_tickers, obs_date)`:
1. Pull `daily_closes(client, tk)` for every universe ticker (cheap: ~128 price-history calls) → cache; compute `regime_series` per ticker → cache the prior-day row for `obs_date`.
2. `good_to_rent = {tk for tk in universe if is_good_renting_weather(prior_row[tk])}`.
3. Pull `chain_frame(client, tk, target_dte, strike_count)` **only for `held_tickers ∪ good_to_rent`** (typically N + ~10–40) → cache.

Methods:
- `chain(tk, day)` → the cached chain (a DataFrame whose `date` column is `obs_date`), or `None` for tickers we didn't pull (non-candidates + non-held). *Behaviorally safe:* a non-good-to-rent ticker gets skipped by `step` whether `chain` returns None (short-circuit) or the regime check catches it — same outcome (no entry). Held tickers always have their chain (for management).
- `spot(tk, day, fallback)` → today's close from the cached series, else `fallback`.
- `settle_price(tk, expiry)` → `None` (the live bot runs every trading day, so it settles expiries at today's spot via `step`'s `settle_spot = spot` default; `None` triggers exactly that fallback).
- `regime_row(tk, day)` → cached prior-day regime row (or None).
- `eligible(tk, day)` → `True` (the live universe has no split/clean-start guard).
- `universe` → the universe list.

### 3. `live/run_daily.py` — the daily paper-step

```
load state (or fresh PortfolioState(cash=starting_capital, positions=[]))
held = {p["ticker"] for p in state.positions}
market = LiveMarket(client, UNIVERSE, held, today)
result = step_one_day(state, market, today, cfg, selector="chop", n_slots=N)
append result.trades to data/live/trades.jsonl
save state to data/live/state.json
print a summary (today's actions, open positions, cash, equity)
```

- `cfg` = the frozen `WheelConfig(put_delta=0.30, call_delta=0.50, target_dte=11, take_profit_pct=0.60, call_min_strike="basis", starting_capital=<account>)`.
- `N` and `starting_capital` (account size) are runner parameters (CLI args, defaults N=5 / $100k) so the same bot is testable across $5k–$500k accounts.
- Store paths under `data/live/` (gitignored).

## Data flow

```
day T:  load state.json ── PortfolioState
        LiveMarket(today): closes(128) -> regime -> good_to_rent -> chains(held ∪ good_to_rent)
        step_one_day(state, LiveMarket, today, cfg, selector="chop", n_slots=N)
        -> append trades -> trades.jsonl ;  save state -> state.json ;  print summary
day T+1: same, resuming from the saved state.
```

## Error handling

- A ticker's Schwab pull fails (non-200) → the adapter raises; the runner **logs and skips that ticker** (drops it from the universe for the day) rather than aborting the whole run — one bad symbol must not stop the bot. A held ticker that fails to pull → keep the position, mark unmanaged this day (log a warning), retry next run.
- `load_state` on a corrupt/absent file → absent = fresh start; corrupt = abort with a clear error (never silently reset a real portfolio).
- `save_state` is atomic (temp + rename).
- No network in `state.py` (pure); `LiveMarket`/`run_daily` do the I/O.

## Testing

- **`state.py` round-trip** (offline): a `PortfolioState` with an open put campaign + an assigned-shares campaign → `state_from_dict(state_to_dict(s))` reproduces every field, including the nested `Contract` (root/expiry/strike/right) and `prev_d`. Atomic save/load round-trips through a tmp file.
- **`LiveMarket` unit** (offline, fake client): a stub client returning the saved GDX fixtures (`live/fixtures/*.json`) for a 1–2 ticker universe → assert `chain`/`spot`/`regime_row` return correct values; a non-good-to-rent, non-held ticker → `chain` returns None (not pulled); a held ticker → `chain` present.
- **`step` runs on `LiveMarket`** (offline, fake client): build a `LiveMarket` from fixtures, call `step_one_day` once → it produces a valid `StepResult` and mutates state without error (proves the seam wiring end-to-end offline).
- **Live smoke** (`live/run_daily.py --smoke`, manual): one real paper day on a tiny 3-ticker universe → prints trades + state, writes the JSON files. Not a pytest.
- Tests run on `.venv-live`: `PYTHONPATH=. .venv-live/bin/python -m pytest live/ -v`.

## Files touched

- `live/state.py`, `live/market_live.py`, `live/run_daily.py` — new.
- `live/tests/test_state.py`, `live/tests/test_market_live.py`, `live/tests/test_step_on_livemarket.py` — new.
- `.gitignore` — `data/live/`.

## Open items

None blocking. Affordability (B3) and snapshot recording (B4) are deliberately after this; B2's deliverable is "the bot completes one live paper day end-to-end and remembers it tomorrow."
