"""Audit run (30-delta / target 30 -- the config the published +58.3% believed it
was testing) and SPY-as-basket-member run (20-delta / target 7). Raw numbers only."""
import pandas as pd
from pathlib import Path
from src.engine_v2.options.wheel import run_wheel, WheelConfig
from src.engine_v2.options.report import wheel_report, format_report

ch = pd.read_parquet("data/options/spy_greeks_eod_all.parquet")
outd = Path("data/options/reports"); outd.mkdir(parents=True, exist_ok=True)
for name, cfg in [
    ("audit_30d_t30", WheelConfig(put_delta=0.30, call_delta=0.30, target_dte=30, ticker="SPY")),
    ("basket_20d_t7", WheelConfig(put_delta=0.20, call_delta=0.20, target_dte=7,  ticker="SPY")),
]:
    res = run_wheel(ch, cfg)
    txt = format_report(wheel_report(res, ch, cfg))
    (outd / f"{name}.txt").write_text(txt)
    print(f"\n=== {name} ===\n{txt}")
