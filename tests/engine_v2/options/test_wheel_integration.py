import os
import pandas as pd
import pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel

FIX = "fixtures/spy_wheel_cycle.parquet"


@pytest.mark.skipif(not os.path.exists(FIX), reason="wheel cycle fixture not built")
def test_wheel_runs_on_real_cycle():
    ch = pd.read_parquet(FIX)
    res = run_wheel(ch, WheelConfig(dte_min=20, dte_max=45))
    assert len(res.equity) > 5
    assert (res.equity > 0).all()               # never blows up
    assert any(t.action == "SELL_PUT" for t in res.trades)
    # equity starts near capital (first bar carries only a small short liability)
    assert abs(res.equity.iloc[0] - 100_000) < 5_000
    # golden: pinned to the committed fixture so a logic-changing refactor breaks it
    assert len(res.trades) == 12
    assert [t.action for t in res.trades] == ["SELL_PUT", "CLOSE_PUT"] * 6
    assert res.final_cash == pytest.approx(102_426.40, abs=0.01)
    assert res.final_shares == 0
