# engine_v2

Leg-agnostic backtest core: WF + CPCV, DSR + FWER gate, Carver sizing,
real cost model, per-regime verdict. See
`docs/superpowers/specs/2026-07-09-engine-v2-design.md`.

## Modules
- `data/` — modern-era bar loader, regime tagger, sealed splits
- `strategy/` — v2 plugin protocol + validator
- `sizing/` — Carver stack + guardrails + NCO allocator (MP-denoise)
- `execution/` — sim + shorting + halt/gap + live-parity stub
- `backtest/` — trial expansion + no-lookahead loop + CPCV folds
- `gate/` — DSR + FWER + per-regime + verdict + sealed persistence

## Run
```
python scripts/build_fixtures.py            # one-shot fixture build
pytest tests/engine_v2/ -v                  # unit + integ + e2e
```
