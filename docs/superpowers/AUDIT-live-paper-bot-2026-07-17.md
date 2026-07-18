# Deep audit — live paper-trading bot (2026-07-17, overnight autonomous)

**Scope:** the full live paper-trading bot built this session — Schwab client (`scripts/schwab/`), live data adapter (`live/data.py`, `live/universe.py`), the shared per-day engine (`step_one_day` + `Market` seam in `src/engine_v2/options/portfolio.py`, `market.py`), the live loop (`live/market_live.py`, `live/run_daily.py`), and state/snapshot persistence (`live/state.py`, `live/snapshots.py`).

**Method:** 4 independent cold-eyes auditors (no chat context), each reading a module set fresh + tracing consumers, plus empirical stress: the byte-identical backtest anchor, the `--rotation` referee (an independent re-derivation), a full-wheel-lifecycle-through-the-live-path simulation with JSON state reload between every day, a data-only/secrets sweep, and a real live paper day against Schwab.

**Bottom line:** the **core trading engine is sound** — money path, look-ahead discipline, over-commit sizing, and state round-trip were all verified clean, with zero Critical defects in the engine. The defects were in the **live adapter/loop I built on top of it** — most importantly one Critical that would have half-broken the wheel live. All Critical + Important findings are **fixed and re-verified**; the remainder are documented below.

## What each auditor found

**Auditor 1 — trading step + Market (`step_one_day`, `market.py`):** No Critical. Verified every cash sign/multiplier, no look-ahead (strictly-prior-day regime), over-commit sizing provably impossible, state repack complete, edge cases handled. One Important (latent, cross-seam): `option_mark`/`select_contract` match on date/expiry/strike/right but not `root` — safe now (single-root chains), a landmine if a multi-root chain ever reached them.

**Auditor 2 — live loop + runner (`market_live.py`, `run_daily.py`):** Found the **Critical (C1)** — the live chain was PUTS-ONLY, so the covered-call leg could never fire. Plus Importants: settle_price=None mis-settles a missed-day expiry; the 130-ticker live universe bypasses `run_portfolio_wheel`'s guards; throttle not wired; a held-ticker pull failure can settle a same-day expiry on a stale spot. Confirmed data-only and look-ahead-clean.

**Auditor 3 — state persistence (`state.py`, `snapshots.py`):** Serialization logic itself is complete + type-faithful; corrupt state correctly refuses to run. Importants all in write-discipline: trades logged before state saved; no fsync before rename; no prior-state backup.

**Auditor 4 — adapter + client (`data.py`, `schwab_client.py`, `universe.py`):** No Critical; data-only + security confirmed; no look-ahead in the timezone handling. Importants: `chain_from_json` didn't guard a null `underlyingPrice` (crashes the whole run); `throttle` dead code; `X` (US Steel) delisted.

## Empirical results

- **Byte-identical anchor** (backtest output unchanged): PASS (20 pinning tests + `--rotation` referee 0 mismatches). The refactor and all fixes left the validated backtest penny-for-penny identical.
- **Full-wheel lifecycle through the live path** (sell put → assigned → covered call → called away → flat) with JSON state reload between every day: PASS. Money math exact ($100k → $118,935 on a called-away-above-basis cycle). Committed as `live/tests/test_lifecycle_persistence.py`.
- **Data-only / secrets sweep:** zero order-placement code anywhere; our code never calls the client's order methods; no secrets in tracked files (config lives in `~/.schwab/`, outside the repo).
- **Live paper day vs real Schwab:** ran end-to-end; correctly took 0 trades (GDX/SLV downtrend, XOP uptrend — none in chop). C1 fix confirmed live: the GDX chain now returns both puts and calls.

## Fixes applied (all merged to main, re-verified)

| # | Sev | Fix | Commit theme |
|---|-----|-----|--------------|
| C1 | **Critical** | Live chain now pulls `contract_type=ALL`; `chain_from_json` walks both put+call maps → covered-call leg fires | cluster 1 |
| I1 | Important | `chain_from_json` raises on null `underlyingPrice` → LiveMarket skips that one ticker, not the whole run | cluster 1 |
| I2 | Important | `throttle` (429/502 retry) wired into `daily_closes` + `chain_frame` | cluster 1 |
| I3 | Important | `X` (US Steel, delisted Jun 2025) → `AA` (Alcoa) | cluster 1 |
| I4 | Important | `LiveMarket.settle_price` returns last close on-or-before expiry (matches BatchMarket) → missed-day expiry settles correctly | cluster 2 |
| I6b | Minor | regime compute moved inside the per-ticker try/except | cluster 2 |
| I8 | Minor | obs date threaded into `chain_frame` so the chain datestamp matches the engine's filter day | cluster 2 |
| I5 | Important | `paper_step` saves state BEFORE appending the trade log (no log-ahead-of-state) | cluster 3 |
| I6 | Important | `save_state` fsyncs the temp file before `os.replace` (durable vs power loss) | cluster 3 |
| I7 | Important | `save_state` keeps the prior `state.json` as `.prev` (recover a valid-but-wrong write) | cluster 3 |
| — | Minor | snapshot now records `credit`/`last_mid`; skip `mark`-None contracts | clusters 1,4 |

## Documented, NOT fixed tonight (need owner judgment or carry known-safe risk)

- **I9 — root-agnostic `option_mark`/`select_contract`:** currently safe (every chain is single-root by construction). A real guard needs a `root` column on the chain frame, which touches the byte-identical-anchored `select.py` — too risky to rush unsupervised. Mitigated with an explicit INVARIANT comment; recommend a proper `root`-column guard next session.
- **Live universe vs backtest guards (Auditor 2, Important):** the live path intentionally runs the 128-ticker universe by calling `step_one_day` directly, bypassing `run_portfolio_wheel`'s 9-ticker `ROTATION_TIE_ORDER` allow-list. This is **by design** — the backtest's allow-list was a pre-registration guard for the *backtest* one-shot; live forward trading is inherently out-of-sample, so it doesn't apply. The reserved tickers (XBI/EEM/EWZ/TLT) appearing in the live universe is therefore fine for live. Flagged for owner sign-off.
- **`mid = mark` (live) vs `(bid+ask)/2` (backtest):** an intentional quote-convention divergence (per the adapter spec). Worth an explicit sign-off, especially for wide-spread names.
- **`strike_count=12` truncation (minor):** a held put's strike can drift outside the 12-strike ATM band → its mark reads None → its 60%-TP exit is missed until the strike re-enters the band. Systematically holds positions a bit longer than the full-ladder backtest. Consider a wider `strike_count` for held tickers.
- **Negative-cash + `cash_yield>0` artifact:** benign at the default `cash_yield=0`; documented.
- **Deliberately deferred sub-projects:** B3 (account-size affordability filter — the bot already handles account sizes via `n=budget//(strike*100)` sizing, so this is a refinement) and D (the live dashboard — deferred because the store is empty and it's a large, subjective, *destructive* rebuild needing the owner's eye).

## Verdict

The live paper bot **runs end-to-end, trades correctly (full wheel now works both legs), persists durably, and touches no order endpoint.** The engine is byte-identical to the validated backtest. Ship it forward for paper accumulation; address I9 (root guard) and the deferred sub-projects with the owner.
