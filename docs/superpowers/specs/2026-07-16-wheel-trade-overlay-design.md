# Wheel Trade Overlay — Design

**Date:** 2026-07-16
**Status:** approved (owner, 2026-07-16)
**Branch:** `wheel-trade-overlay` (off `main`)

## Motivation

The Wheel page shows the score and not the plays. You can read that a position was
assigned; you cannot see price walking into the strike, or what price was doing
around a stop-out. The split-chop lever was falsified on forced-exit whipsaw — a
class of finding currently excavated from tables rather than seen.

This makes the Wheel page price-first: candles for the traded underlying, with every
position from the blotter drawn on them.

### Origin, and why TradingView is not used

The owner asked about integrating "the TradingView API". Investigation found no
legitimate TradingView market-data API exists — their exchange data is licensed for
display inside their own product. What exists is Lightweight Charts and the Charting
Library (renderers you feed your own data), widgets (opaque iframes), Pine alert
webhooks (paid, push-only), and unofficial scrapers.

Scrapers are ruled out on this project's own terms: a reverse-engineered feed that
can change or die silently is a data-integrity hole in exactly the place this project
is careful, and ThetaData already covers the need.

What the owner actually wanted — candles with entries, exits and strikes — needs no
TradingView at all. It is achievable with Plotly and data already on disk.

## Scope

**In:**
- Candle + trade-overlay chart on the Wheel page
- Strip six sections from the Wheel page
- One shared outcome-colour rule, used by both the blotter and the chart

**Out:**
- Per-run result persistence. The overlay renders for the in-session run only;
  `data/wheel_runs.csv` keeps summary rows, not trades. Persisting runs is a separate
  project and is not bundled here.
- Lightweight Charts / any TradingView component
- The Run, Regime and History pages
- A multi-ticker selector (see [Forward seam](#forward-seam-basket-run))

## Page shape

Ten sections become five:

```
knobs + Run                    (66–148, unchanged)
P&L │ Sharpe │ MaxDD │ vs SPY  (177–192, kept)
━━━ CANDLES + TRADES ━━━       (new — the main visual)
Year-by-year return            (235–237, kept)
Trade blotter                  (260–273, kept)
```

`dashboard/views/wheel.py` **shrinks on net** despite gaining the chart. This is why
the work stays on one page rather than adding a second.

### Deletions — `dashboard/views/wheel.py`

| Lines | Section | Rationale |
|---|---|---|
| 155–175 | Basis vs plain table | `log_run` (147) already writes `plain_total_return`; the comparison survives on the History page |
| 194–199 | Days-flat warning | Folded into the `vs SPY` tile tooltip — see below |
| 201–223 | Defense stats | Re-derivable from the blotter (`campaign_id`, `outcome`) |
| 225–233 | Equity vs buy & hold | Superseded by the candle chart. This is the intended swap |
| 239–249 | Wheel stats grid | Counts of blotter rows |
| 251–258 | Realized DTE | A proof chart; determinism is already established |

Consequent cleanup:
- Remove `_arm_row` (156–159), `rows`, `cmp_df`
- Remove the `buy_hold_curve`, `spy_curve` import (227)
- `s = rep.stats` (195) is retained, used only by the days-flat tooltip
- `res_plain` / `rep_plain` become unused **at render time only**. Keep the session
  tuple shape unchanged — `log_run` still consumes `rep_plain` inside the Run block
  (147). Changing the tuple would touch History for no gain.

**Not deleted:** `charts.equity_curve`, `charts.underwater`, `charts.monthly_heatmap`
are all still used by `views/run.py`. `band_lo` / `band_hi` still feed the knobs
caption (90).

### Days-flat tooltip

The `vs SPY buy & hold` tile (184–186) already takes `help=`. Append the flat-days
figure to that string.

Rationale: the owner kept the verdict tile and cut the standalone warning, but the
warning qualifies the verdict — a bot flat 30% of a window is being compared against a
benchmark invested 100% of it, and looks worse than it is. The tooltip costs no
vertical space and keeps the caveat attached to the number it qualifies.

## Components

### `dashboard/trades.py` (new)

Pure pandas. **No streamlit import.** This is the renderer-agnostic module: if the
renderer is ever swapped, this survives untouched.

```
overlay_frame(blotter: pd.DataFrame, window_end: pd.Timestamp) -> pd.DataFrame
```

Input is `position_log(res, cfg)` output — columns `opened, closed, instrument,
strike, expiry, qty, credit, cost_to_close, realized_pnl, pct_of_credit, outcome,
days_held, campaign_id`.

Output — one row per drawable position:

| Column | Value |
|---|---|
| `x0` | `opened` |
| `x1` | `closed`, or `window_end` when `closed` is `NaT` |
| `y` | `strike` — uniform across every instrument (see below) |
| `group` | colour group key from `outcome_group` (below) |
| `open_ended` | `closed.isna()` — renders dashed |
| `hover` | preformatted: instrument, strike, outcome, credit, realized P&L, days held, campaign |

**`y = strike` is uniform.** `position_log` writes `strike=astrike` on SHARES rows
(report.py:347, :360, :382) — the assignment strike, which is the cost basis. There
is no NaN-strike case to handle and no entry price to derive.

**Open-ended rows are detected by `closed.isna()`, never by outcome text.** Two
outcomes leave a position open: `"Open"` (shares still held, :380) and `"Settled at
mark"` (option live at window end, :370). Matching on the outcome string would
silently truncate `"Settled at mark"` segments.

### `dashboard/bars.py` (new)

```
load_bars(ticker: str, start, end) -> pd.DataFrame     # @st.cache_data
```

Thin adapter over `src/engine_v2/data/source.py` (`UNIVERSE_PATH` →
`fixtures/bars_etf_universe_2010_2026.parquet`, `load(tickers, start, end)`). Not a
new parquet reader. Returns single-ticker OHLCV indexed by date; returns an empty
frame when the ticker is absent.

**The parquet does not cover every tradeable ticker.** It holds 20 ETFs (DBC EEM EFA
GLD HYG IEF IWM LQD QQQ SHY SLV SPY TLT VNQ XLE XLF XLI XLK XLU XLV), while
`_sources()` (28) offers whatever option chains are on disk. XOP has a chain and no
bars. This is a live case, not a hypothetical — the graceful degrade below is
load-bearing.

The options chain (`ch`) carries underlying price per date but close-only, so it
cannot substitute for candles.

### `dashboard/charts.py`

```
candles_with_trades(bars: pd.DataFrame, overlay: pd.DataFrame) -> go.Figure
```

- `go.Candlestick` from bars
- One `go.Scatter(mode="lines")` **per colour group**, segments None-separated within
  the trace. This makes the Plotly legend a free filter: click "Assigned" to isolate
  assignments. No custom widget.
- Open-ended segments dashed
- `theme.apply(fig)` — no hardcoded colours, per theme.py's stated rule

### `dashboard/theme.py`

```
GROUP_COLORS: dict[str, str]   # {"Lost money": NEGATIVE, "Assigned": WARNING,
                               #  "Kept premium": POSITIVE, "Open": MUTED}
```

Colours only. The rule that decides *which* group a row is in is domain logic, not
palette, and lives in `trades.py` — theme.py's stated job is "one Plotly template plus
the palette", and a P&L-sign branch does not belong in it.

### `outcome_group` — in `dashboard/trades.py`

```
outcome_group(realized_pnl, outcome) -> str
```

`_style_blotter`'s existing branch order (wheel.py:292–301), extracted verbatim:

1. `realized_pnl < 0` → `"Lost money"`
2. `outcome == "Assigned"` → `"Assigned"`
3. `realized_pnl` not null → `"Kept premium"`
4. else → `"Open"`

**The chart adopts the blotter's rule rather than inventing a taxonomy.** The owner
already reads red/amber/green off the blotter daily; a second scheme would mean two
to hold in mind, and they would eventually disagree about the same trade.

The per-group opacities in `_style_blotter` (0.22 / 0.20 / 0.14, tuned for the dark
theme) stay local to `_style_blotter`. Only the group decision and the base colours
are shared.

### `dashboard/views/wheel.py` — the new block

`position_log` is currently called at 262, at the bottom of the page. Move that call
above the chart and feed **both** the chart and the blotter table from it — one call,
two consumers.

**The window is `res.equity.index[0]` … `res.equity.index[-1]`** — the run's actual
trading days. Not the date_input values (the user can pick a Saturday) and not
`ch["date"]` (the chain spans expiries beyond the equity window). `window_end` is the
same value fed to `overlay_frame` for open-ended segments, so the dashed segments stop
exactly at the last candle rather than floating past it.

Between the verdict row and Year-by-year:

```
st.subheader("Trades on price")
w0, w1 = res.equity.index[0], res.equity.index[-1]
bars_df = bars.load_bars(rep.ticker, w0, w1)
if bars_df.empty:
    st.info(f"No daily bars on disk for {rep.ticker} — candles unavailable.")
else:
    st.plotly_chart(
        charts.candles_with_trades(bars_df, trades.overlay_frame(blotter, w1)),
        width="stretch")
```

### What the chart shows

An assigned put at 450 ends exactly where its SHARES row at 450 begins — same level,
adjoining dates — so a campaign draws as one continuous line that changes colour at
the assignment, with the covered call's strike running in parallel above it. The wheel
draws itself.

## Forward seam (basket run)

Today one run is one ticker (`rep.ticker`), so no selector renders. When the basket
run lands there will be multiple reports, and the selector will pick a **report**, not
filter a frame.

No no-op ticker argument is added now: `overlay_frame` takes a blotter, not a ticker,
because the caller already knows which report it holds. Nothing half-built is left on
screen waiting for a future that may not arrive.

## Edge cases

| Case | Behaviour |
|---|---|
| No run in session | Existing `if stashed is not None` guard (151) already covers it |
| Zero trades | Candles render, no overlay traces |
| Ticker has a chain but no bars (XOP) | `st.info`, candles skipped, rest of page unaffected |
| Position open at window end (`closed` is `NaT`) | Segment runs to window end, dashed |
| Run window wider than bars coverage | Slice clamps; candles cover what exists |
| Fixture ticker | Maps to `"SPY"` (71), which is in the parquet — works |

## Tests

**New** — `tests/test_wheel_dashboard.py` plus a pure module for `trades.py`:

- `overlay_frame`: option row → `y == strike`, `x0/x1 == opened/closed`
- `overlay_frame`: SHARES row → `y == strike` (the assignment basis), row retained
- `overlay_frame`: `closed` is `NaT` → `x1 == window_end`, `open_ended` True
- `overlay_frame`: `"Settled at mark"` is treated as open-ended (regression guard —
  matching on the outcome string instead of `closed.isna()` would truncate it)
- `outcome_group`: pnl<0 → Lost money; Assigned → Assigned; pnl≥0 → Kept premium;
  null pnl → Open
- **Parity**: `_style_blotter`'s tints agree with `GROUP_COLORS` — guards the exact
  drift this extraction exists to prevent
- `load_bars`: returns the requested ticker's OHLC columns; empty frame for a ticker
  absent from the parquet
- `candles_with_trades`: returns a `go.Figure`; one candlestick trace plus one trace
  per present group; empty overlay → candles only
- Wheel page renders the candle chart after a run

**Deleted:**

- `test_wheel_run_shows_basis_vs_plain_table` (70–79). The table it asserts is removed
  by design, so the test is **deleted, not weakened**. Softening a test until it
  passes is how a suite goes green while guarding nothing.

**Must stay green, unchanged:**

- `test_wheel_page_runs_and_renders_metrics` — `len(at.metric) >= 4` still holds; the
  verdict row it guards is kept.

Full suite: `.venv/bin/python -m pytest -q`

## Renderer decision (recorded)

Plotly, not TradingView Lightweight Charts.

- LWC's advantages do not apply here. Its crosshair feel is marginal and its real
  strength is 100k+ bars; this window is ~4,100 daily bars at most.
- LWC's costs are real: an iframe via `st.components.v1.html`, JSON serialization, a
  second theme system, a markers API that moved to a plugin between v4 and v5, and
  charts opaque to the existing AppTest render tests — the new view would be
  untestable by the suite that covers everything around it.
- The renderer is not the work. The join is, and the join is identical either way.
  `dashboard/trades.py` is renderer-agnostic by construction, so a future swap
  replaces `charts.candles_with_trades` alone.

## Out-of-band note (not part of this work)

Flagged 2026-07-16 and left untouched: `scripts/pull_watchdog2.sh`,
`pull_supervisor_loop.sh` and `pull_resilient.sh` carry uncommitted fixes (atomic
singleton lock, process-death-only restart policy) documenting the 2026-07-15
port-25503 competing-terminal incident. They sit in the working tree on
`router-regime-params`, a branch whose last commit reads *"negative in-sample, do not
ship"*. The basket run depends on the bulk pull, which depends on that watchdog. If
that branch is binned, the fixes go with it. They belong on `main` in their own
commit, independent of the DTE-30 verdict.
