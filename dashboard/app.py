"""Wheel Bot live monitor -- entry point.

  .venv-live/bin/python -m streamlit run dashboard/app.py

The old backtest workbench (Run / Wheel / Chameleon / Regime / History) is
retired; its view modules still sit in dashboard/views/ but are no longer wired
in. Importing dashboard.live renders the live paper-trading monitor."""
from dashboard import live  # noqa: F401  (renders on import)
