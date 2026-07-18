# Deep audit — live-bot additions (2026-07-18)

**Scope:** the trading-path code added *since* the 2026-07-17 audit, ahead of a 10-day unattended run across 25 accounts:
1. **Chop-quality gates** — `is_good_renting_weather` (ma-spread / fast-spread / down-only-fast-fall) + `regime_series` `fast_spread` column; `WheelConfig` fields; the `selector=="chop"` gate in `portfolio.py`; the good-to-rent gate in `market_live.py`.
2. **25-account matrix** — `live/accounts.py`, `live/run_daily.py` multi-account loop, per-account persistence.
3. **Intraday exit-only TP manager** — `live/intraday.py`, `live/marks.py contract_quotes`/`occ_symbol`, `live/run_intraday.py`.

**Method:** 3 independent cold-read auditors (one per subsystem, no chat context), each tracing consumers and verifying design intent against the actual code, plus an empirical battery run by the controller: byte-identical anchor (full backtest suite), multi-account isolation + intraday-integration stress, and a hands-on repro of every claimed defect before fixing it.

**Bottom line:** one **Critical** and one **Important** found, both **fixed + regression-tested**. The gates subsystem came back fully clean (no Critical/Important). All findings were reproduced by the controller before fixing (the 2026-07-17 audit had a subagent fabricate a report — raw findings are never trusted unverified).

## Findings + fixes

| # | Sev | Subsystem | Defect | Fix |
|---|-----|-----------|--------|-----|
| **C1** | **Critical** | intraday | `contract_quotes` skipped a leg only on `None` bid/ask, not `0.0`. A two-sided `0/0` Schwab quote (halt, first seconds after open, thin option) became `Mark(0,0,0)` → the TP check `ask <= (1-TP)*credit` is always true at ask 0 → the leg was "closed" for commission only and dropped permanently; the 5pm EOD run never re-opens it (state is source of truth). Silent book corruption, no error. Diverges from EOD, which pre-filters `bid>0 & ask>0`. **Reproduced end-to-end.** | `live/marks.py` — guard `bid <= 0 or ask <= 0` too (matches the EOD filter). Regression test `test_contract_quotes_skips_zero_quote`. |
| **I1** | Important | matrix | The per-account loop in `run_daily.py` had no `try/except` — one account's throw (bad chain, edge case) aborted every *later* account in the fixed order, silently dropping them for that day in an unattended run. | `run_daily.py` (and, same class, `run_intraday.py`) — wrap each account's step in `try/except`, log `label`+error, `continue`. |
| M-gate | Minor | gates | `regime_series`'s empty-frame early-return column list omitted `fast_spread`, so empty vs populated frames had inconsistent schemas (latent trap for future concat/reindex). | Added `fast_spread` to the empty-frame column list. |
| M1 | Minor | intraday | `live_marks` (dashboard MTM path) used dict access `c["root"]`, latent `TypeError` on a `Contract` dataclass. | Use `_contract_field`. |
| M2 | Minor | intraday | Booked `Trade` date was midnight-normalized, losing the intraday fill time in the log. | Book at `pd.Timestamp(now)`. |

## What the auditors verified clean (design intent held)

- **Gates byte-identical when default-None** — all three guards short-circuit before touching new columns; `fast_spread` is read by label everywhere (never positionally); regime states are never cached, so no stale-schema path. Down-only sign correct (`fast_spread < -t` rejects falls, keeps flat/up); boundaries handled; NaN can't reach the comparison (warmup slice guarantees non-NaN). Live pull-gate and backtest routing-gate use the same threshold — cannot disagree.
- **Matrix** — each account gets its own `PortfolioState` (no aliasing); the shared `LiveMarket` is read-only across accounts (no cross-contamination); held = union of all accounts' held so every held position's chain is present; state saved before trades; capital/N wired per-account with no fixed-value leak; 25 unique store dirs.
- **Intraday** — TP math byte-identical to the EOD close (ask trigger, `buy_cost`, drop-empty-PUT); exits only (no open/assign/expiry/call-sell); expiry stays EOD; `occ_symbol` correct incl. fractional/high strikes; market-hours gate DST-safe and inclusive at 09:30/16:00; no double-close / no 5pm race (temporal separation by construction).

## Empirical (controller)

- **Byte-identical anchor:** full backtest suite **465 passed** before and after the fixes (gates default-off; the `fast_spread` column changes moved nothing).
- **Multi-account isolation:** closed one account's put intraday (cash moved by exact `buy_cost`), a second account stayed untouched with distinct objects — no aliasing.
- **C1 repro → fix:** `0/0` quote closed the put for commission-only pre-fix; post-fix it is skipped, position intact, cash unchanged.
- Live suite **56 passed** (incl. the C1 regression).

## Verdict

The new trading-path code is sound for the unattended run: gates are correct and backtest-identical, the 25 accounts are isolated and correctly wired, and the intraday manager matches the EOD close exactly — with the one Critical (phantom free close on a dead quote) and the one Important (per-account isolation) fixed and pinned.
