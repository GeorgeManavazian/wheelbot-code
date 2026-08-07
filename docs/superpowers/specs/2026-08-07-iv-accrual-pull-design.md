# Universe-wide IV accrual pull — design (2026-08-07)

Status: **approved by owner 2026-08-07**, not yet implemented.

## The problem this exists to fix

`IVHistory.rank` needs `MIN_RANK_OBS = 150` observations inside a `RANK_WINDOW = 252`
trailing window before it returns anything but `None`. An IV observation for a ticker
can only exist on a day that ticker's option chain was pulled.

`live/market_live.py:73` pulls chains for held tickers **union** that day's weather-gate
passers only — measured at **12 of 547 on 2026-07-31**. So an average ticker is pulled on
~2% of trading days: roughly **5-6 observations per year against a requirement of 150**.

The forward path therefore cannot sustain a history it is handed. Buy two years, launch,
and the purchased observations roll out of the trailing window while ~5/yr accrue behind
them; about a year after launch every name crosses back under 150, `rank()` returns
`None`, all candidates tie at `NEUTRAL_IV_RANK`, and selection falls through to
`market.universe.index(tk)` — **the ranker silently becomes universe order**, with
`entry_ranked_iv_unknown` in the log as the only trace.

This is vendor-independent. No purchased history changes what Schwab accrues afterward.
Fixing accrual is what makes a backfill a one-time purchase rather than a permanent
subscription.

Background: `vault/10 Live Paper Bot/2026-08-07 — The forward IV series starves on
gate-passers only.md`.

## Scope

**In:** a separate universe-wide daily pull that computes and persists one IV observation
per ticker per day.

**Out (deliberate):**

- The read side. `run_daily` keeps `iv_history=None`; `rank_by="iv_rank"` continues to
  raise via the guard committed in `7c8ea48`. Nothing can consume observations for ~150
  trading days, and the read path is better built later against real accrued data than
  against fixtures.
- Any change to trading behavior. Selection, strikes, marks and settlement are untouched.
- Coverage. This moves no ticker into the rankable column; 108/547 stands until history is
  purchased or ~150 days accrue.

## Owner decisions this design encodes (2026-08-07)

1. **A separate accrual pull**, not a widening of the existing trading pull. The trading
   pull keeps its ~12-ticker `chain_attempts` denominator, so `zombie_check` thresholds
   need no re-derivation and no trading decision depends on 45x heavier API work.
2. **Its own runner, gated on the trading snapshot's marker.** Trading gets the window and
   the API budget first.
3. **One JSON file per trading day.** Mirrors the `chain_store` idiom; the git mirror sees
   one new file per day and never a modified one.
4. **Failure is judged on the pull, not the yield.** A low observation count is a real
   property of thin expiry ladders, not an outage.
5. **Write side only** (see Scope).

## Architecture

| File | Change |
|---|---|
| `live/data.py` | **new** `otm_put_frame()` |
| `live/iv_store.py` | **new** — `save_iv_day()` / `load_iv_day()` |
| `live/run_iv_accrual.py` | **new** — the runner |
| `scripts/wheelbot_tick.sh` | **new block** after the chain-snapshot block |

No existing function's behavior changes. `chain_from_json`, `_CHAIN_COLS`, `chain_store`,
`market_live.py` and `run_daily.py` are all read-only to this work.

### `otm_put_frame(client, ticker, target_dte, obs_date=None)`

PUT-only, `strike_range=OUT_OF_THE_MONEY`, request window `[today+8, today+15]` for
`target_dte=11`. Calls `chain_from_json` unchanged, then attaches `rate` and `div_yield`
columns read from the chain header (`interestRate`, `dividendYield`).

Mirrors `otm_call_frame` (A4), including its reasoning: `strike_range=OUT_OF_THE_MONEY`
gets exactly what the exchange lists with no `strike_count` guess, so the ~0.30-delta put
is guaranteed present. The existing `chain_frame` uses `strike_count=12`, spot-centred,
measured at about ±3.8% — a 0.30-delta put at 11 DTE can sit further OTM than that on a
low-vol name, which would mean pulling 547 chains and still missing the one contract each
is for.

The request window is `+8/+15` rather than exactly `derived_band(11) = (9, 14)` because
Schwab's `daysToExpiration` need not agree with `(expiry - today).days` at the edges. One
day of slack each side, with the exact band filter applied downstream by
`select_contract`.

**`PUT`-only is load-bearing**, the mirror image of A4's `CALL`-only note: these rows must
never reach `market._chains`, or every account sharing the market gains far-OTM puts as
tradeable entry candidates. The accrual runner builds no `LiveMarket` and never calls
`add_chain_rows`, so this is structural, not a convention.

### `live/iv_store.py`

`data/live/iv/YYYY-MM-DD.json`, one file per trading day, ~547 records.

Two contracts copied from `chain_store`, for the same reasons stated there:

- **Atomic write** (tmp + `os.replace`). A reader must never see a half-written file.
- **`load_iv_day` returns `None`, never a stale dict**, when the file is absent,
  unreadable, or stamped with a different obs date.

Path via `live/paths.in_state`, so the store lands under `data/live/` and rides
`sync_state('data/live', ...)` to the mirror with no sync change. Verify this during
implementation rather than assuming it.

Record shape — **the solver inputs are stored alongside the answer**, so a solver-version
change can rebuild the series from disk without re-pulling or re-purchasing anything:

```
ticker, expiry, strike, dte, delta, bid, ask, mid,
underlying, rate, div_yield, iv, source
```

`source` is the provenance stamp: source / solver version / **and the selection config**,
e.g. `"schwab-rth/bs-v1/d30/dte11"` — see the `put_delta`/`target_dte` hazard below for why
the last part is not optional. `IVHistory.append` refuses to extend a series whose stamp
differs, per the 2026-08-07 provenance ruling.

**Assumption, stated rather than asked:** the PROPOSED scale-vs-vendor amendment to that
ruling stays unimplemented. It binds nothing while we accrue from a single source, and the
overlap measurement that would justify it can only be collected against a live vendor
subscription. Revisit when a backfill is actually purchased.

### `live/run_iv_accrual.py`

Own RTH window, own marker (`.ivaccrual-$TODAY`), own exit code. Refuses to start until
today's trading snapshot marker exists.

Save deadline mirrors `run_chain_snapshot.SAVE_DEADLINE = 1605`: a pull finishing past it
is discarded rather than saved, because post-close quotes must not be stamped as RTH.

## Data flow, per ticker

```
otm_put_frame(client, tk, target_dte)
  → chain_from_json    (existing: drops bid<=0/ask<=0, missing delta,
                        nonStandard, multiplier != 100)
  → + rate, div_yield columns from the chain header
select_contract(day, obs, "P", cfg.put_delta, cfg.target_dte, tk)
  → applies its own derived_band(11) = DTE 9-14, then nearest |delta| to 0.30
  → None  ⇒  no observation today for this ticker (correct, not an error)
implied_vol_put(price=mid, underlying=..., strike=..., dte=...,
                rate=rate/100, div_yield=div_yield/100)
  → one record, appended to today's file
```

`select_contract` is called verbatim rather than reimplemented. That is what makes the
stored value the IV of **the contract the bot would actually have sold that day** — the
quantity every prior measurement was taken on. `IVHistory.from_chains` states the
requirement: recording a chain-wide average or an ATM proxy instead "silently redefines"
the statistic.

A date with no selectable put contributes **no observation**, never a NaN, so gaps shorten
a history instead of poisoning the percentile. Same rule as `from_chains`.

`implied_vol_put` refuses ITM puts by design. The OTM-only pull means it should never see
one; the runner still skips rather than propagates if it ever does, so an unexpected input
cannot kill the day.

### ⚠ Hazard: rate and dividend yield are PERCENT

Schwab returns `interestRate` and `dividendYield` as percentages, the same convention as
its per-contract `volatility`. `scratchpad/diag_schwab_iv_fit.py:31` divides both by 100
(`a['R']=a['r']/100.0; a['Q']=a['q']/100.0`). `implied_vol_put` takes decimals.

Passing `4.0` where `0.04` is meant does not raise and does not warn. It returns a
plausible IV that is wrong on every contract, permanently, in a series whose whole purpose
is internal consistency. **Gets a dedicated test.**

### ⚠ Hazard: `put_delta` and `target_dte` are part of the scale

The runner takes `put_delta` and `target_dte` from `FROZEN` (0.30 / 11), so the recorded
contract is the one the live bot would sell. That coupling is the point — and it means
**changing either config value mid-window redefines the observed quantity exactly as a
solver change would.** A series that switches from "the 0.30-delta put at 11 DTE" to "the
0.20-delta put at 7 DTE" is two scales in one window: every later observation shifts
relative to its own past and the rank pins toward 0 or 1, with nothing in the logs looking
wrong.

This is not hypothetical. `_STATUS` records an open question over whether the strategy is
0.30 delta or the 0.20 the owner described on 2026-08-03, and the sweep's top arm was
delta 0.40 / DTE 7.

The provenance stamp must therefore encode them — `"schwab-rth/bs-v1/d30/dte11"` or
equivalent — so `IVHistory.append`'s existing refusal fires on a config change the same way
it fires on a source change. Settle the exact stamp format during implementation; the
requirement is that a `put_delta` or `target_dte` change cannot silently append.

### ⚠ Hazard: bytecode caching on same-length constant edits

Recorded 2026-08-07, cost ~20 minutes. A mutate/test/revert cycle inside one second, with
a replacement constant of identical byte length, leaves the `.pyc` validating as fresh on
`(mtime, size)` — Python keeps running the mutant while `inspect.getsource` reads the
reverted source. Clear `src/**/__pycache__` and `touch` the file after any same-length
constant edit during mutation checks.

## Error handling

| Case | Behavior |
|---|---|
| `select_contract` returns `None` | No record. Counted and logged, **never a failure** — AOS was measured with an in-band expiry on only 17% of days. |
| One ticker's chain pull raises | Skipped, counted, run continues. |
| Failure ratio over `zombie_threshold` | Save what exists, **exit 1**, no marker → the next in-window tick resumes. |
| Retry inside the window | `load_iv_day(obs)` first; re-pull **only tickers with no record yet today**. A retry after 500 successes costs 47 calls, not 547. |
| Trading snapshot marker absent | Refuse, exit non-zero, no marker. |
| Pull finishes past the save deadline | Discard; do not stamp post-close quotes as RTH. |

Failure is judged on the **chain failure ratio**, `(attempted - ok) / attempted >=
zombie_threshold`, reusing `zombie_threshold` from `live/config.json` (read via
`load_run_config()`) — the same doctrine and the same direction as `zombie_check`'s chain
branch: judge the feed that failed, never the derived quantity. Observation count is recorded and logged but
is never a failure condition, because a low count is ladder depth, not an outage.

Rejected: judging today's observation count against a trailing baseline. It is
self-calibrating and would catch a silent yield regression, but it is blind for its first
20 days — exactly when a new pull is most likely to be wrong. Worth revisiting once a
baseline exists.

Rejected: never failing. A total endpoint outage would then be indistinguishable from a
week of thin ladders, which is the 2026-07-24 shape (all 547 tickers failed, exit 0, the
wrapper wrote the done-marker, the day was recorded complete and became unrecoverable).

The runner's exit code gates **only its own marker**. The tick's `FAIL` variable is not set
by this block, so an IV outage cannot alert-storm and cannot mark a trading day failed.

## Tick placement

New block after the chain-snapshot block in `scripts/wheelbot_tick.sh`:

- weekdays, inside the window, `.ivaccrual-$TODAY` absent, **and `.chainsnap-$TODAY`
  present**
- marker written only on exit 0, so a failed pull retries on every remaining in-window tick
- does not set `FAIL`

Placement after the chain-snapshot block matches the existing reasoning for placing that
block after the intraday block: a time-sensitive job is never queued behind a long pull.

## Testing

TDD, and every test mutation-verified — the pattern used for `iv_solve` and `iv_rank` on
2026-08-07. A test that passes against deliberately broken code is worse than no test.

1. `otm_put_frame` issues the right request (PUT, OUT_OF_THE_MONEY, `+8/+15` window) and
   attaches header `rate`/`div_yield`. Offline, fixture payload.
2. **Percent→decimal.** A fixture carrying `interestRate: 4.0` solves identically to one
   hand-fed `0.04`. Mutation: remove the `/100`, confirm this test goes red.
3. **Selection parity.** The contract chosen from an `otm_put_frame` equals the one
   `IVHistory.from_chains` picks from the same rows.
4. Store round-trip; `load_iv_day` returns `None` for a file stamped with another date.
5. **Resume.** Given a partial file for today, the runner requests only the missing
   tickers.
6. **Failure judging.** Attempted 547 / ok 12 → exit 1, no marker. Attempted 547 / ok 541
   with only 200 observations written → exit 0.
7. **Tick gating.** The IV block does not run without `.chainsnap-$TODAY`, via the existing
   `WHEELBOT_FAKE_*` hooks in `live/tests/test_tick_script.py`.
8. **Config is in the stamp.** A record written under `put_delta=0.30` and one written
   under `0.20` carry different `source` values, and `IVHistory.append` raises when the
   second is appended to the first.

Baseline to hold: 669 passing, 0 failing.

## Cost and consequences

- **~547 chain calls/day instead of ~12**, roughly 5 minutes of pulling, inside a window
  that already reserves 15:20-15:50 and starts only after the trading snapshot saves.
- **~90 KB/day of new state**, ~23 MB/year, one new file per day, never modified — the
  cheapest possible git-mirror diff.
- **The counter reads 0 until day 1.** The 252-day clock only advances on days the bot
  runs. The owner has kept day 1 deferred, so this ships, deploys, and accrues nothing
  until the timer is enabled. Stated here so it is on the record rather than discovered
  later.

## References

- `vault/10 Live Paper Bot/2026-08-07 — The forward IV series starves on gate-passers only.md`
- `vault/10 Live Paper Bot/2026-08-07 — The IV provenance rule, the solver is the scale.md`
- `vault/10 Live Paper Bot/2026-08-07 — Sourcing the IV history, four measurements.md`
- `vault/10 Live Paper Bot/2026-08-06 — Wiring IV rank into the live bot, build brief.md` — item 4
- `7c8ea48` — brief items 1-3: `LiveMarket.iv_rank`, `iv_rankable()`, guard on the live path
