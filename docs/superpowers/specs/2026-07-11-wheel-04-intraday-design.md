# Design: Wheel sub-project 4 — intraday take-profit

- **Date:** 2026-07-11
- **Status:** design — awaiting review
- **Part of:** the Wheel-on-SPY backtester. Sub-project 4 of ~5.
- **Scope:** refine take-profit *timing* using intraday option marks. Entries/assignment/expiry stay EOD. Two-pass (EOD decisions → pull held-contract intraday → re-run). Resolution configurable, **default hourly**.

## Decisions (owner-confirmed 2026-07-11)

- **A** — intraday affects **take-profit only**. Everything else (strike selection at EOD, assignment, expiry) is unchanged.
- **B** — **two-pass**: (1) EOD wheel → which contracts are held on which days; (2) pull intraday quotes for only those contracts; (3) re-run with intraday-TP resolution.
- **C** — resolution configurable, **default hourly** (`interval_ms=3_600_000`); 5-min / 1-min available.
- **D** — TP fills at the **crossing bar's ask** (cross the spread), consistent with the EOD engine.

## Why / the gap

At EOD the engine checks TP once (that day's ask). A short can decay to the 50% target intraday — especially short-DTE — then close back above; EOD misses it, or fills at a worse EOD price than the intraday moment it crossed. Intraday marks let TP fire at the first bar the ask crosses the threshold. On the current bull fixture the wheel TP's out 6 times purely on EOD; intraday will move some of those exits earlier and to better prices.

## The three pieces

### 1. Intraday option puller (`scripts`/`options`)
`pull_option_intraday(client, contract, start, end, interval_ms=3_600_000) -> pd.DataFrame`
- Hits `/v3/option/history/quote?symbol=<root>&expiration=<ISO>&strike=<strike×1000>&right=<P|C>&start_date&end_date&interval=<ms>` (single contract). **Strike is encoded ×1000** (ThetaData 1/10-cent units) — verify at build time against a known contract.
- Returns a tidy frame indexed by tz-naive ET `timestamp`, columns `bid, ask, mid` (RTH). Skips no-data (472) like the EOD puller.
- A `held_contracts(result) -> list[(Contract, first_date, last_date)]` helper walks a `WheelResult` trade log: each short position spans from its SELL_* date to its CLOSE_*/ASSIGNED/EXPIRED date. These are the (contract, window) pairs to pull intraday.

### 2. Intraday-TP resolution in `run_wheel`
- `run_wheel(chain, cfg, intraday=None)` gains an optional `intraday: dict[Contract, pd.DataFrame]` (contract → its intraday bid/ask frame). Backward-compatible: `intraday=None` → today's exact EOD behavior.
- In the daily loop, when a short is held on date `d` and `intraday` has that contract: scan that day's bars in time order; the **first** bar with `ask <= (1 - tp) * credit` fires TP — buy-to-close at that bar's ask + commission, recorded as a `Trade` stamped with the bar's timestamp. If no intraday bar crosses that day, fall through to the existing EOD check (so a same-day EOD cross still fires). If the contract isn't in `intraday`, pure EOD (unchanged).
- Expiry/assignment logic is untouched (still EOD/expiry). Same-day re-entry (policy B) still applies after an intraday TP close.

### 3. Two-pass orchestration
`run_wheel_intraday(chain, cfg, client_or_intraday, interval_ms=3_600_000) -> WheelResult`
- Pass 1: `run_wheel(chain, cfg)` (EOD) → `held = held_contracts(result)`.
- Pull intraday for each held (contract, window) → build the `intraday` dict (cache to gitignored parquet; skip already-pulled).
- Pass 2: `run_wheel(chain, cfg, intraday=intraday)` → return that result.
- For tests/offline, accept a pre-built `intraday` dict instead of a live client.

## Dashboard

Add an **"Intraday take-profit (hourly)"** checkbox to the Wheel page. When on and intraday data is available for the selected dataset, run `run_wheel_intraday`; else run EOD. Show a note when intraday isn't built for the chosen data. (The committed fixture ships with a small intraday sample so the toggle works out of the box.)

## Data / timing caveat (subscription lapses in ~2 weeks)

The *code* is proven now on a small real intraday sample + synthetic tests. Running intraday-TP on the **full history** needs an intraday pull for all held contracts — a follow-on grab that depends on the EOD pull finishing + a full EOD wheel run to enumerate held contracts. Flag: **do that pull before the subscription lapses.** Hourly keeps it small (~7 bars/contract/day).

## Where code lives

- `src/engine_v2/options/intraday.py` — `pull_option_intraday`, `held_contracts`, `run_wheel_intraday`.
- `src/engine_v2/options/wheel.py` — add the optional `intraday` param to `run_wheel` (backward-compatible).
- `dashboard/views/wheel.py` — the toggle.
- Fixture: `fixtures/spy_wheel_intraday_sample.parquet` (one held contract's hourly quotes) for tests.

## Testing

- `pull_option_intraday` (live smoke, skip-if-down): one held contract → hourly frame with bid/ask, tz-naive ET.
- `held_contracts`: from a synthetic `WheelResult`, returns the right (contract, window) list.
- **Intraday TP (synthetic):** a short whose intraday ask dips below the threshold mid-day → TP fires at that bar's timestamp + ask, NOT at EOD; the same series without an intraday cross → EOD behavior unchanged; `intraday=None` → byte-identical to current `run_wheel`.
- Two-pass integration on the committed intraday sample: `run_wheel_intraday` differs from EOD only in TP timing/price, and the trade log is coherent.
- Dashboard: the toggle renders + runs with the sample.

## Non-goals

- No intraday entries or intraday assignment (EOD/expiry only). No full-history intraday pull as a deliverable (follow-on operation). No sub-minute data. No change to sizing/fills beyond the TP bar price.

## Open choices (defaulted; flag to change)

- **Hourly default** (`interval_ms=3_600_000`); finer available per-run.
- TP scans **ask** bars (we buy to close). Bid path is irrelevant for a short-close.
- Intraday cache in gitignored `data/options/intraday/`; small sample committed as a fixture.
