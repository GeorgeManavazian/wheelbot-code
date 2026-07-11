import os
import pandas as pd
import pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel
from src.engine_v2.options.report import wheel_report, format_report

FIX = "fixtures/spy_wheel_cycle.parquet"

@pytest.mark.skipif(not os.path.exists(FIX), reason="wheel cycle fixture not built")
def test_format_report_has_sections():
    ch = pd.read_parquet(FIX)
    cfg = WheelConfig(dte_min=20, dte_max=45)
    rep = wheel_report(run_wheel(ch, cfg), ch, cfg)
    txt = format_report(rep)
    assert isinstance(txt, str)
    for token in ("CAGR", "Sharpe", "Max drawdown", "buy-hold", "Year", "assignment"):
        assert token.lower() in txt.lower()
    assert "2024" in txt          # year-by-year row present
