# Dashboard: basis floor always-on, auto plain-wheel benchmark

Date: 2026-07-15
Status: design — approved by owner, pending spec review
Scope: `dashboard/views/wheel.py` + `tests/test_wheel_dashboard.py` only. Engine untouched.

## Motivation

Every wheel test the owner runs uses the basis floor ("never rent below cost") —
it is the one mechanic that transformed results (GDX +17.5k → +155.6k) and the
strategy of record. The defense dropdown offering plain / basis / gated variants
is dead weight: the owner always picks basis, and the gated variants are
falsified or redundant.

Two findings drove this (session 2026-07-15):

- **Entry gate is dead.** On the plain wheel it was mixed-to-negative
  (SPY 38.3k→35.8k, GDX 17.5k→4.7k). On the basis wheel it is a pure no-op:
  the engine's own `days_entry_gated` counter reads **0** on SPY and GDX because
  the basis floor already keeps the wheel holding assigned shares through quiet
  declines instead of opening new put campaigns — so a new campaign never opens
  on an unpaid-decline day for the gate to block. The basis floor *subsumes* the
  entry gate. Verified: plain SPY has 11 put-opens on unpaid-decline days, basis
  SPY has 0; plain GDX 47, basis GDX 0.
- Roll gate is noise-level; stop gate is the only gate that earns its keep, and
  it belongs to the stop mechanic, not the basis floor.

So: make the basis floor non-optional on the dashboard, and always show it
against the plain wheel (the benchmark the owner actually cares about).

## What this is NOT

- **Not an engine change.** `call_min_strike` stays a `WheelConfig` field.
  Plain wheel stays fully reachable in code. The referees
  (`audit_defense_execution.py`), the router A/B (`run_regime_router.py`), the
  gates A/B (`run_regime_gates.py`), and every `scripts/` provenance runner
  keep working unchanged. This is dashboard-only.
- Not a change to the Chameleon router (it consumes the plain+basis wheel via
  its own path and is out of scope here).

## Design

### 1. Inputs — remove the choice

Delete from `dashboard/views/wheel.py`:
- the `DEFENSES` dict,
- the `_HELP` dict,
- the stale-key reset (`if st.session_state.get("w_defense") not in DEFENSES`),
- the `defense_name = st.selectbox("Defense", ...)` widget and its `st.caption`.

Every run builds the config with the basis floor hardcoded:

```python
cfg = WheelConfig(starting_capital=float(capital), put_delta=put_delta,
                  call_delta=call_delta, target_dte=int(target_dte),
                  take_profit_pct=tp_slider / 100.0, ticker=ticker,
                  call_min_strike="basis")
```

All other inputs stay exactly as they are (ticker, start/end, put/call delta,
target DTE, take-profit slider, capital, intraday checkbox).

Since no gate is ever armed, `cfg.any_regime_gate` is always False → no
`regime_states` is built or passed. Drop the `states = ...` block and pass
`regime_states=None` (or omit) to both runs.

### 2. Run — two arms every click

On the Run button, execute the backtest twice on the same chain/window/knobs:

- **Basis arm** — `call_min_strike="basis"` — the strategy of record.
- **Plain arm** — same config with `call_min_strike=None` — the benchmark.

Both arms honor the intraday checkbox identically (both use
`run_wheel_intraday` when on, both use `run_wheel` when off) so the comparison
is apples-to-apples on fill timing.

Stash both results in session state:
`st.session_state["_wheel_result"] = (res_basis, rep_basis, res_plain, rep_plain, cfg, ch)`.

### 3. Show — comparison table on top, detail below

**New headline comparison table** (always rendered when a result exists), one
row per arm/benchmark, columns P&L / Return / Sharpe / MaxDD:

| Arm | P&L | Return | Sharpe | MaxDD |
|---|---|---|---|---|
| Basis wheel | … | … | … | … |
| Plain wheel | … | … | … | … |
| Buy-hold {ticker} | … | … | — | … |
| Buy-hold SPY | … | … | — | … |

Buy-hold rows come from the existing `rep.benchmark_underlying` /
`rep.benchmark_spy` (basis arm's report — benchmarks are arm-independent). SPY
row omitted when the traded ticker is SPY or no SPY chain is on disk.

**Equity chart:** overlay the basis equity curve AND the plain equity curve on
the existing buy-hold chart (extend the `bench` dict passed to
`charts.equity_curve`, or add the plain curve as an extra series). One glance
shows the floor's effect.

**Detail sections bound to the BASIS arm only** (plain is just a benchmark line,
needs no blotter): the existing Wheel-stats metrics, Realized-DTE chart,
campaign/defense stats, and Trade blotter all read from `rep_basis` / `res_basis`.

**Delete the regime-gate diagnostics block** (`if getattr(rep, "gates", None)`)
— no gate is ever armed now, so it can never render. Dead code.

Keep the campaign-level "Defense stats" block for the basis arm (campaign count
and win rate are informative for the floor; rolls/stops will read 0, which is
correct and harmless).

### 4. History log

`log_run` logs the **basis arm** as the run of record. Set `"defense": "basis"`
(fixed string) since there is no longer a choice. Add a `"plain_total_return"`
field carrying the plain arm's total return for at-a-glance reference in the
History tab. No other history schema changes.

## Testing

`tests/test_wheel_dashboard.py` currently asserts the dropdown and single-arm
render. Update to:

- assert the "Defense" selectbox is **gone**,
- assert a run produces both a basis and a plain result (two-arm stash shape),
- assert the comparison table renders all present rows (basis, plain, buy-hold),
- assert the equity chart includes both a basis and a plain series,
- assert the gate-diagnostics block is gone (no gate ever armed),
- keep/adjust existing assertions for the basis-arm detail sections.

Follow TDD: write the failing tests against the new render contract first, then
implement. Full suite (`make test`) must stay green.

## Success criteria

- No "Defense" control on the wheel dashboard; every run uses the basis floor.
- Every run shows basis vs plain vs buy-hold(s) in one table without any toggling.
- Engine and all non-dashboard consumers unchanged; `make test` green.
