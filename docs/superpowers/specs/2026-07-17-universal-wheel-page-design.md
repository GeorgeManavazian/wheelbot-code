# Universal wheel dashboard page — design

**Date:** 2026-07-17
**Project:** Chop-scanner rotation (dashboard surface) — `code/etf-bot`
**Status:** design, pending implementation plan
**Depends on / consumes:** the rotation engine (`src/engine_v2/options/portfolio.py`, `run_portfolio_wheel`, `ROTATION_TIE_ORDER`), regime state (`regime/state.py`, `regime/data.py`), the wheel report layer (`report.py`), the dashboard chart/guard helpers, the chameleon page (`dashboard/views/chameleon.py`) as the structural pattern.

## Problem

The chop-scanner rotation bot (plain+basis wheel, `selector="chop"`, N concurrent campaigns over a fixed universe) is only reachable from the CLI. The owner wants it on the dashboard **in place of the single-ticker Wheel page** — same idea (a wheel bot), but universal: no ticker picker, it scans the whole universe. All the normal wheel knobs stay adjustable, plus the rotation knob (N). And an easy-to-read summary: P&L, win rate, number of trades/campaigns, average % per win.

One honesty problem must be designed in, not bolted on: the in-sample **win rate is 100%**, because the basis floor never *realizes* a loss — it converts a losing position into held shares that wait for recovery. A naive "100% win rate" tile would badly mislead the owner (who is the judge). The page must show the win rate in a way that surfaces the hidden risk.

## Non-goals

- Engine changes. The page consumes `run_portfolio_wheel(..., selector="chop", n_slots=N)` as-is.
- Config sweeps / grids. One run per click, like the wheel page.
- Touching the reserved five (XBI EEM EWZ TLT ARKK). They are not in `ROTATION_TIE_ORDER`; the engine refuses them; the page never presents them.
- Logging runs to the History tab (the old wheel page did; the universal page does not in v1 — History keeps working on existing data). Revisit later if wanted.
- The hourly/intraday path. EOD only, matching the rotation engine.
- Keeping single-ticker wheel testing in the UI. Owner chose to replace it outright (still CLI-runnable if ever needed). The Chameleon page (single-ticker router) is unaffected.

## Frozen-by-design (not knobs)

- `selector="chop"` and `call_min_strike="basis"` are always on — they are what make this *this* bot.
- Universe = `ROTATION_TIE_ORDER` (the 9 dev tickers), shown read-only.

## Architecture

New `dashboard/views/universal_wheel.py`, structured like `dashboard/views/chameleon.py`. It takes over the **"Wheel" nav slot** in `dashboard/app.py` (same title "Wheel", same `url_path="wheel"`). The old `dashboard/views/wheel.py` view and its single-ticker dashboard test are removed — verified safe: nothing imports `dashboard.views.wheel` except itself; the History page uses the separate `dashboard.wheel_history` log module, which stays.

Page flow (mirrors chameleon):
1. Read-only universe banner ("Scanning 9 tickers: SPY GDX SLV XOP AAPL AMZN NVDA META FB"), sourced from `ROTATION_TIE_ORDER`.
2. Knobs: put delta, call delta, TP %, target DTE, capital (same widgets as the old wheel page) + an **N-slots slider (1–9)** + date-range start/end.
3. XOP pre-2020-07-01 split warning carried over; short-window guard (`< 5` trading days → warn + stop).
4. Run → build `WheelConfig(..., call_min_strike="basis")` from the knobs, load the 9 chains + `regime_series(closes_for(t))` states, call `run_portfolio_wheel(chains, cfg, states, selector="chop", n_slots=N)`, stash the `PortfolioResult` in session state.

## The portfolio-aware campaign helper

The existing `campaign_table` cannot be reused: it scans trades assuming contiguous per-campaign ordering (portfolio campaigns interleave across tickers by date) and reads an int `final_shares` (portfolio's is a dict). New helper in `report.py`:

`portfolio_campaign_table(result, cfg, last_spots) -> pd.DataFrame` — groups `result.trades` by `campaign_id`, one row per campaign:

- `campaign_id`, `ticker` (contract root), `opened`, `closed`, `n_trades`
- `pnl_realized` — summed signed cash flows: `+` premiums (SELL_PUT/SELL_CALL, `price×mult×n − comm×n`), `−` closes (CLOSE_PUT/CLOSE_CALL, `price×mult×n + comm×n`), `−` assignment (`strike×mult×n`), `+` call-away (`strike×mult×n`).
- `shares_held` — net shares still on the book (assigned minus called-away); `open_at_end = shares_held > 0`.
- `collateral` — the opening put's `strike × mult × contracts` (capital-at-risk basis for %).
- `pnl_mtm` — `pnl_realized + shares_held × last_spots[ticker]` (mark-to-market, includes unrealized).
- `pct_return` — `pnl_realized / collateral`.

`last_spots` = `{ticker: last underlying close}` from the chains, passed by the page.

## Summary + honest twin win-rate

Tile rows at the top (big, readable):

- **Row 1:** P&L · Finished-rentals win % · Sold-today win % · Avg % per win
- **Row 2:** # campaigns · # trades · Max drawdown · Idle % (cash)

Definitions:
- **Finished-rentals win %** = of campaigns with `open_at_end == False`, share with `pnl_realized > 0`. This is the honest reading of "100%": every *finished* rental won.
- **Sold-today win %** = of **all** campaigns, share with `pnl_mtm > 0`. An open, underwater campaign counts as a loss, so this drops below 100% whenever the bot holds underwater shares.
- **Caption under the two** (the warning light): *"The gap between these two is money hidden in shares the bot is still holding — if they're equal, nothing's hidden."*
- **Avg % per win** = mean `pct_return` over winning realized campaigns.
- **# campaigns** = `result.n_campaigns_opened`; **# trades** = `len(result.trades)`.
- **Max drawdown** from `result.equity` via `metrics_simple.max_drawdown`.
- **Idle % (cash)** = `result.days_flat / len(result.equity)` — surfaces the capital-sits-in-cash weakness.

Below the tiles:
- **Equity curve** — `charts.equity_curve(result.equity, {})` (account value over time).
- **Per-ticker entries** — a small table: ticker → number of campaigns opened (count SELL_PUT-that-open-a-campaign per root).
- **Campaign blotter** — `portfolio_campaign_table` sorted newest-first: ticker, opened, closed, `pnl_realized`, `pct_return`, and an "open (holding shares)" flag on stuck rows; currency/percent formatted.

## Error handling

- No chains on disk for the universe → warning + `st.stop()`.
- Window `< 5` trading days → warning + stop.
- A ticker with too little history for a regime state → the engine already treats unknown state as not-good-to-rent (excluded); no crash.
- Reserved tickers cannot be selected (no picker) and are refused by the engine regardless.

## Testing

1. **`portfolio_campaign_table` unit** (load-bearing): a fixture `PortfolioResult` with two campaigns — one closed-and-profitable, one still holding shares — asserts two correct rows: `pnl_realized`, `open_at_end` on the stuck one, `collateral` from the opening put, `pnl_mtm` folding in `shares_held × last_spot`.
2. **Twin win-rate unit**: from that table, finished-rentals win % counts only closed campaigns; sold-today win % counts an open-underwater campaign as a loss; the two numbers differ when a stuck campaign is below water.
3. **Page smoke test** (mirror `test_chameleon_dashboard.py`): drive the real app, switch to the Wheel slot, run once, assert no exception and that the summary tiles render.
4. **Reserved-five unreachable**: the page presents no reserved ticker; the engine refuses one (pinned at page level).
5. **Removal regression**: after deleting the old `wheel.py` view, the full suite stays green, including the History page.

## Files touched

- `dashboard/views/universal_wheel.py` — new page.
- `dashboard/views/wheel.py` — removed (replaced).
- `dashboard/app.py` — Wheel nav slot repointed to `universal_wheel.render`.
- `src/engine_v2/options/report.py` — `portfolio_campaign_table` helper.
- `dashboard/charts.py` — reused as-is (`equity_curve`); no change expected.
- tests — the units above + the page smoke test; remove the old single-ticker wheel dashboard test.

## Open items

None blocking. History-tab logging for the universal page is deferred (v1 does not log; History keeps working on existing data).
