"""Wheel Bot live monitor -- entry point.

  .venv-live/bin/python -m streamlit run dashboard/app.py

The old backtest workbench (Run / Wheel / Chameleon / Regime / History) is
retired; its view modules still sit in dashboard/views/ but are no longer wired
in. main() must be CALLED on every rerun -- a cached import would render only
the first run and then blank."""
from dashboard.live import main

main()
