# Design: Wheel sub-project 6 — dashboard v2 (date range, trade blotter, run history)

- **Date:** 2026-07-12
- **Status:** design — awaiting review
- **Scope:** three owner-requested dashboard features + the engine-layer logic they need. No change to the wheel engine's behavior.

## Features

1. **Date range** on the Wheel page — test the wheel on any sub-window (isolate 2020, the 2022 bear, etc.).
2. **Trade blotter** — replace the raw event table with a position-level log: each option round-trip + each share round-trip with **realized P&L**, the way a trader logs trades. Built + reconciled in the engine layer (`report.py`) so the numbers are trustworthy.
3. **History tab** — every Run auto-logs config + dates + results to a file; a browsable table (newest first) with a "clear" button and a **"load config back into the form"** action.

## 1. Trade blotter — `position_log(result, cfg) -> pd.DataFrame`

Reconstruct positions from `result.trades` (the event log). Two instrument kinds, one unified table sorted by `opened`:

**Option round-trips** — pair each `SELL_PUT`/`SELL_CALL` with its terminating event (`CLOSE_*`, `*_EXPIRED`, `ASSIGNED`, `CALLED_AWAY`):
- `opened, closed` (dates), `instrument` ("PUT"/"CALL"), `strike`, `expiry`, `qty`,
- `credit` = net premium received at open = `bid*mult*qty − commission*qty`,
- `outcome` ∈ {"Took profit","Expired worthless","Assigned","Called away"},
- `cost_to_close` = `ask*mult*qty + commission*qty` for TP; `0` for expired/assigned/called-away,
- **`realized_pnl`** = `credit − cost_to_close`,
- `pct_of_credit` = `realized_pnl / credit`, `days_held`.

**Share round-trips** — pair each `ASSIGNED` (bought `100*qty` shares at the put strike) with the next `CALLED_AWAY` (sold at the call strike):
- `instrument` = "SHARES", `opened`=assignment date, `closed`=called-away date, `strike` shown as the entry (assignment) strike, `qty`,
- `credit` = `−assign_strike*mult*qty` (cash out to take the shares), `cost_to_close` = `+sale_strike*mult*qty` (cash in),
- **`realized_pnl`** = `(sale_strike − assign_strike)*mult*qty`, `outcome`="Called away", `days_held`.
- A trailing assignment never called away (held at window end) → one open SHARES row with `closed`=NaT, `realized_pnl`=unrealized MTM at the last spot (labelled outcome "Open").

**Reconciliation (the trust check):** `sum(realized_pnl over closed positions) + open-position MTM ≈ final equity − starting_capital`, to within rounding. This is a TEST, not just a display — it proves the blotter accounts for every dollar.

Also keep the raw event view available (a `format_report`/debug path); the dashboard shows the blotter by default.

## 2. Date range — Wheel page

- Two `st.date_input`s (`wheel_start`, `wheel_end`) defaulting to the loaded data's full span (`chain.date.min()/max()`).
- On Run, filter `ch = ch[(ch.date >= start) & (ch.date <= end)]` before `run_wheel`. A put whose expiry falls beyond `end` simply never resolves in-window → residual-settled at window end (already handled) — honest for a windowed test.
- Guard: empty/too-short window → friendly message, no run.

## 3. Run history

**Persistence — `dashboard/wheel_history.py`:**
- `RUNS_PATH = "data/wheel_runs.csv"` (gitignored).
- `log_run(record: dict) -> None` — append a row (create with header if absent). Record: `ts, data_source, start, end, put_delta, call_delta, dte_min, dte_max, take_profit, capital, intraday, total_return, max_drawdown, sharpe, pnl, n_trades, n_assignments`.
- `load_runs() -> pd.DataFrame` (newest first; empty frame if no file). `clear_runs()` deletes the file.

**Wheel page:** after a successful Run, call `log_run(...)` with the config + window + `rep`/`res` results.

**History tab — `dashboard/views/wheel_history.py::render()`** (new nav entry):
- Show `load_runs()` as a table, newest first, formatted (P&L as $, returns/DD as %).
- **"Clear history"** button → `clear_runs()`.
- **"Load config"** — a selector of past runs; on pick, write the run's config into `st.session_state` keys the Wheel page reads as widget defaults, and navigate to the Wheel tab. (Streamlit: set `st.session_state["w_pd"]=...` etc. before the Wheel page builds its widgets; guard so it only applies once.)

## Where code lives
- `src/engine_v2/options/report.py` — add `position_log`.
- `dashboard/views/wheel.py` — date range + blotter display + `log_run` call + read session-state config defaults.
- `dashboard/wheel_history.py` — persistence.
- `dashboard/views/wheel_history.py` — History tab.
- `dashboard/app.py` — add the History nav entry.

## Testing
- `position_log`: on a synthetic result with a TP, an expiry, and an assignment→called-away cycle — assert per-row `realized_pnl` values and outcomes; assert **reconciliation** (sum ≈ total P&L) on the committed `spy_wheel_cycle.parquet` run.
- `log_run`/`load_runs`/`clear_runs`: round-trip a record to a temp path; newest-first ordering; clear removes it.
- Dashboard: Wheel page renders with date inputs + a blotter table after Run; History tab renders the logged table; load-config sets session state. (AppTest, on the fixture.)

## Non-goals
No change to `run_wheel` behavior. No per-run equity-curve storage in history (just the summary row + config). No multi-user/db (a local CSV). No dividends in share P&L.

## Open choices (defaulted; flag)
- History is a **CSV** (simple, inspectable) at `data/wheel_runs.csv`.
- Blotter is **position-level** by default; raw events remain available in code but not shown.
- Load-config repopulates the form but does NOT auto-run (you review, then Run).
