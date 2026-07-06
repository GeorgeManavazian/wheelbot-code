# ETF Bot Research Dashboard — Design Spec

**Date:** 2026-07-05
**Status:** Approved by owner (session 2026-07-05)
**Depends on:** `2026-07-05-research-engine-design.md` (engine is built; this reads its outputs)

## Goal

A local, read-only Streamlit dashboard for exploring screening-batch results: what strategies were tested, which won, whether wins are robust or curve-fit, and how individual runs behaved.

Owner is a near-beginner coder — code stays simple and readable; one small file per view.

## Scope

- **v1 covers the ETF bot only.** CL bot added later if its engine writes the same leaderboard format.
- **Read-only.** No verdict buttons, no vault writes. Verdicts (keep/kill/iterate) continue to be recorded as vault notes through work sessions.
- **Build timing:** after the first real screening batch produces a leaderboard CSV in `results/`. Until then this spec is the contract.
- Runs on the laptop via `streamlit run dashboard/app.py`. No server, no deployment (see vault decision "2026-07-05 — Deployment deferred (Coolify plan)").

## Non-goals (v1)

- No live-trading monitoring (Grafana's job, post-deployment).
- No exam-data access of any kind.
- No persistence of recomputed results — cache only.
- No auth, no multi-user, no hosting.

## Core decision: recompute on demand (Approach A)

The batch runner persists only summary rows (leaderboard CSV). Equity curves are NOT stored. When a view needs a curve, the dashboard rebuilds the strategy object from its leaderboard row (name + params) and re-runs that single backtest on playground data, cached with `@st.cache_data`.

**Why over persisting artifacts (rejected Approach B):** zero engine changes; curves always match the current engine exactly (byte-identical engine rule extends to what the owner sees); no second on-disk format to drift; data is tiny (daily bars, 20 ETFs, ~2,800 bars) so one re-run costs ~1s.

**Also rejected:** storing results in the Obsidian vault — vault holds conclusions (verdict notes), repo holds numbers; bulk data was deliberately moved out of the vault 2026-07-04.

## Architecture

```
etf-bot/
└── dashboard/
    ├── app.py             <- entry point, page registration, batch picker
    ├── loader.py          <- find/read leaderboard CSVs in results/
    ├── recompute.py       <- leaderboard row -> strategy object -> cached run_backtest
    └── views/
        ├── leaderboard.py <- sortable table, honesty flags, luck-warning banner
        ├── run_detail.py  <- equity curve, drawdown, year-by-year, trade stats
        ├── plateau.py     <- parameter heatmap (reuses engine plateau_table())
        └── compare.py     <- 2-4 runs overlaid, metrics side by side
```

- Multi-page Streamlit app; each view file exposes one `render(...)` function.
- Dashboard imports engine code directly (same repo): `run_backtest`, `summarize`, `plateau_table`. Zero engine modifications.
- Charts: Plotly (interactive hover/zoom).

## Data flow

1. `loader.py` scans `results/` for leaderboard CSVs; if several batches exist, sidebar picker selects one.
2. Leaderboard and plateau views read the CSV directly — instant.
3. Run detail / compare: `recompute.py` maps a leaderboard row's `name` + param columns back to a strategy class (strategy registry keyed by `name`), instantiates with params, runs `run_backtest` on playground data, caches the `BacktestResult`.
4. **Playground data only.** The dashboard never uses the `--exam` path; the exam seal is untouched.

## Views

1. **Leaderboard** — full leaderboard table, sortable by any metric; honesty columns (`n_trades`, `sample_flag`, `positive_years/total_years`) always visible; the engine's multiple-testing luck warning rendered as a permanent banner; crashed runs (non-empty `error`) in a separate red section. Row select → run detail.
2. **Run detail** — one run: equity curve, drawdown chart, year-by-year return table, summary metrics, trade count vs significance floor.
3. **Plateau** — pick strategy family + parameter; heatmap of Sharpe (or chosen metric) across the grid via engine's `plateau_table()`. Spike vs plateau visible at a glance.
4. **Compare** — pick 2–4 runs; equity curves overlaid (normalized to common start), metrics table side by side.

## Error handling

- Empty/missing `results/` → friendly "no batches yet — run a screening first" screen.
- Crashed strategy rows → shown explicitly, never silently dropped.
- **Stale-leaderboard guard:** on recompute, compare recomputed Sharpe/CAGR to the CSV row; mismatch beyond CSV rounding (relative tolerance 1e-6) → loud banner "leaderboard stale — engine has changed since this batch ran; re-run the batch." Doubles as an honesty guardrail: the dashboard can never show numbers the current engine wouldn't produce.
- Unknown strategy `name` in CSV (plugin renamed/deleted) → leaderboard row still displays; detail view explains why recompute is unavailable.

## Testing

- pytest on the logic layer: `loader` (finds/parses CSVs, empty-dir case), `recompute` (round-trip: leaderboard row → strategy → metrics match the row).
- Views verified by running the app; visual layer stays thin, logic lives in `loader.py`/`recompute.py`.

## Build order

1. `loader.py` + tests.
2. `recompute.py` (registry, cache, stale guard) + tests.
3. Leaderboard view + app shell.
4. Run detail view.
5. Plateau view.
6. Compare view.
