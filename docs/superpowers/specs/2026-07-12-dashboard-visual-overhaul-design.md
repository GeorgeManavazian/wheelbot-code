# Design: Dashboard visual overhaul (dark theme, Plotly, leaderboard)

- **Date:** 2026-07-12
- **Status:** design — awaiting review
- **Affects:** `dashboard/`, `.streamlit/config.toml`, run persistence
- **Builds on:** `2026-07-12-wheel-06-dashboard-v2-design.md` (merged `ec61375`)

## The ask

Owner: "I want the dashboard to just be better. It looks too plain and too simple." Plus, in
the same session: color-code the trade blotter, stop showing raw snake_case identifiers, and
add a leaderboard of every configuration ever tested.

## Diagnosis (verified against code)

The dashboard is plain for one concrete reason: **every chart is `st.line_chart` or
`st.bar_chart`** — Streamlit's default renderer. Five charts, both pages. Plotly is a declared
dependency (`plotly>=5.20`) and is **imported zero times**. The theme is light
(`.streamlit/config.toml`, `base = "light"`).

This is not a framework problem. The engine is untouched by this work.

**Why the polish vanished.** The 2026-07-06 polish session shipped friendly names, family
colors, and a monthly heatmap. The 2026-07-10 "strip the noise, don't scrap" rewrite replaced
the view layer and took them with it. But that decision killed *conceptual* noise — two stacked
engines, the CPCV/DSR gate, PASS/WATCH/SHELF verdicts, CSV round-trips. Nothing in it argued for
plain charts. The polish was collateral damage, not the intent.

**Consequence for this design:** visual work does not re-litigate the July 10 decision. Adding
verdicts, scores, gates, or knobs would. This spec adds none.

**Root cause of the regression:** the friendly names lived scattered in view code, so a view
rewrite silently ate them. This spec centralizes them in one module so the next rewrite has one
file to notice.

## Scope

Three surfaces, all of which already exist. Every question the owner wants answered already has
its data computed and shipped; only the presentation is missing.

| Owner's question | Surface |
|---|---|
| "Did it work?" / "Am I ready to trade this?" | Run page |
| "What is it actually doing?" | Wheel page |
| "Which of my configs was best?" | Leaderboard page |

"Did it work" and "am I ready to trade this" are the same page. The second is the first asked
honestly — real costs, worst year, gap vs buy-and-hold.

## Visual direction

"Modern quant" — Linear/Vercel-grade dark. Selected by the owner from four mockups (terminal /
neon fintech / glassmorphic / modern quant). Restrained accents, real typography, no glow.
Chosen over the neon "Instagram" look because glow and dense tables do not coexist, and the
blotter and leaderboard are dense tables.

Palette:

| Token | Value |
|---|---|
| Background | `#0e1117` |
| Panel | `#161b24` |
| Border | `#232a36` |
| Text | `#e6edf3` |
| Muted text | `#7d8798` |
| Accent | `#4f9dfd` |
| Positive | `#3fb950` |
| Negative | `#f0836c` |
| Warning / assigned | `#e3b341` |

## Foundation

Three new modules, no engine changes.

**`.streamlit/config.toml`** — flip to the palette above (`base = "dark"`).

**`dashboard/theme.py`** — one Plotly template: dark paper/plot backgrounds, subtle grid,
tabular numerals, the positive/negative pair, consistent margins and fonts. Every chart applies
it. Single source of chart styling.

**`dashboard/labels.py`** — the single raw→display map, plus a `humanize(df)` helper that renames
columns for display. Covers blotter columns (`cost_to_close` → "Cost to close", `pct_of_credit` →
"Credit kept", `days_held` → "Days held", `qty` → "Contracts"), regime columns (`mean_ret` →
"Mean return", `dimension` → "Dimension"), and all leaderboard columns (`put_delta` → "Put Δ",
`dte_min` → "DTE min", `n_assignments` → "Assignments", `ts` → "Run at", …). Also
`_RIGHT_WORD` moves from shouty `PUT`/`CALL` to `Put`/`Call`.

Every `st.line_chart` / `st.bar_chart` call is removed in favor of themed Plotly figures.

**Interface contract:** views never construct chart styling or column names inline. They call
`theme.apply(fig)` and `labels.humanize(df)`. A view that hardcodes a color or a header is a bug.

## Stage 1 — Run page

Restyled (data already exists): hero tiles; equity curve vs SPY and 60/40; year-by-year Sharpe
decay; year-by-year return; regime table.

New panels:

1. **"vs Buy & Hold" hero tile.** The gap, as a headline number. Free — already computed.
   Deliberately prominent: it is the number that stops the dashboard flattering the owner. The
   vault already records that the plain wheel loses to buy-hold SPY over nine years; that fact
   should be visible on every run, not buried in a caption.
2. **Underwater drawdown panel.** Depth *and duration* of pain, sharing the equity curve's
   x-axis. Free — derived from the equity series.
3. **Monthly returns heatmap.** Restored from the 2026-07-06 polish. Free — derived from the
   equity series.
4. **Cost sensitivity sweep.** Re-runs the backtest at spread levels `[0, 1, 2, 5, 10]` bps
   (borrow held at the form's value) and tabulates CAGR and Sharpe at each, so the owner can see
   the spread at which the edge dies. Five re-runs. **The only expensive panel: it is behind an
   explicit button, never automatic.** Firing it on every run would make the page crawl.

**Gate: the owner reviews Stage 1 running before Stages 2 and 3 are built.** He asked to see it
before deciding what else to change, and stages 2–3 inherit this shell — mistakes here propagate.

## Stage 2 — Wheel page

Same shell. Blotter and stats get the real work.

**Trade blotter.** Column headers via `labels.py`. Rows tinted by outcome (green kept the
premium, amber assigned, red lost money) — owner chose row tinting over number-only coloring,
because the blotter is scanned rather than read. Outcome becomes a colored pill. Realized P&L
colored and signed. Instrument becomes a badge, with `Shares` visually distinct from `Put`/`Call`
so assignment legs stop hiding among the option rows — those are the rows where the wheel bleeds.

**"Credit kept" column.** `pct_of_credit` rendered as a scannable inline bar. How much premium is
actually retained is the wheel's entire thesis, and it is currently a raw column that reads as
noise.

**Wheel stats become tiles**, replacing the run-on text sentence (`Puts sold 47 · calls 12 ·
assignments 3 · …`).

**New: strike-and-assignment chart.** SPY price with put/call strikes as horizontal lines,
assignments and called-aways as markers. This is the panel that lets the owner *watch* the
strategy do something dumb instead of inferring it from a table. Data (chain + trades) is already
in hand.

## Stage 3 — Leaderboard page

Replaces the History page. **One page, not two:** sorted by date it is a history; sorted by P&L
it is a leaderboard. Two pages over one CSV would be exactly the duplication that made this
project noisy before.

**Unified run store — `dashboard/run_store.py`, superseding `wheel_history.py`.** Today only the
Wheel page logs runs (`data/wheel_runs.csv`); the Run page logs nothing, so no ETF strategy
backtest has ever been recorded. Both pages will write to one store with a shared schema plus a
per-bot config blob.

**Honest limitation, stated up front:** the leaderboard **starts from now**. Configurations run
before this change were never persisted and cannot be reconstructed. The owner has been told.

Page contents:

- Table of every logged run across both bots, human column names. Sortable on any column;
  filterable by bot (ETF / Wheel) and by strategy name.
- Multi-select runs → **overlay their equity curves** on one chart.
- **Scatter: Sharpe vs. max drawdown**, one dot per run, so tuning stops being a table-squint.
- Load-a-run's-config-back-into-the-form (carried over from the current History page).

**The one engine-adjacent change in this spec:** the run store persists each run's equity series,
not just its scalar metrics. The current CSV stores only scalars, so curves cannot be overlaid.

### Decision on record: plain leaderboard, no trial correction

A leaderboard over every configuration ever tried sets `K_effective = N`. The 2026-07-10 decision
to park the honesty gate rested explicitly on `K_effective = 1`, and stated: *"The gate matters
again only when we go back to searching over many strategies."* A leaderboard is that condition.
Best-of-N Sharpe is inflated by construction — N configs of pure noise still produce an impressive
winner.

The owner was offered an "honest leaderboard" (trial counter + deflated-Sharpe column, ~30 lines,
using inputs the leaderboard already has) and **chose the plain leaderboard**. Recorded here
deliberately, not buried.

Mitigation, at zero cost to this build: the run store persists everything a deflated-Sharpe column
would need. Adding it later is a column, not a rebuild.

## Explicitly out of scope

No verdicts. No scores. No PASS/WATCH/SHELF. No new strategy knobs. No CPCV/DSR/FWER/NCO. No
frontend rewrite — this stays Streamlit. TradingView Lightweight Charts was considered and
rejected: it is a JS library, the stack is Python, and Plotly already does every chart in this
spec (strike lines, markers, heatmap, underwater).

## Testing

- `labels.py` — every column emitted by the blotter, regime table, and run store has a mapping;
  a test asserts no raw snake_case reaches a rendered table.
- `theme.py` — figures carry the template; no view hardcodes a color.
- `run_store.py` — round-trip a run (scalars + equity series) for both bot types; schema
  migration from the existing `wheel_runs.csv` does not crash on old rows.
- Derived metrics (underwater series, monthly return table) — unit-tested against a known equity
  curve.
- Existing suite stays green (131 tests at `ec61375`).

## References

- `docs/superpowers/specs/2026-07-10-backtest-workbench-design.md`
- `docs/superpowers/specs/2026-07-12-wheel-06-dashboard-v2-design.md`
- Vault: `07 ETF Bot/Decisions/2026-07-10 — Backtest workbench shipped, strip not scrap`
- Vault: `07 ETF Bot/Decisions/2026-07-10 — Refocus, build the bot, park the gate`
- Mockups: `.superpowers/brainstorm/30550-1783885719/content/`
