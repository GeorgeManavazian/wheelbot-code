# etf-bot-engine-v2

## Run the workbench

```
.venv/bin/python -m streamlit run dashboard/app.py
```

`archive/` holds the parked honesty-gate (CPCV/DSR/FWER/NCO/verdict) and the v1
engine -- moved there 2026-07-10, not deleted. The live tree under `src/`,
`dashboard/`, and `tests/` is only the gate-free backtest workbench.

## Engine v2

See `src/engine_v2/README.md`. Spec: `docs/superpowers/specs/2026-07-09-engine-v2-design.md`.
