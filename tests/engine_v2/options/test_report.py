import os
import pandas as pd
import pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel
from src.engine_v2.options.report import spy_buy_hold, wheel_stats, wheel_report

FIX = "fixtures/spy_wheel_cycle.parquet"
pytestmark = pytest.mark.skipif(not os.path.exists(FIX), reason="wheel cycle fixture not built")

def _run():
    ch = pd.read_parquet(FIX)
    cfg = WheelConfig(target_dte=40, put_delta=0.30, call_delta=0.30)
    return run_wheel(ch, cfg), ch, cfg

def test_spy_buy_hold_shape():
    _, ch, _ = _run()
    bh = spy_buy_hold(ch, 100_000)
    assert bh.iloc[0] == pytest.approx(100_000)          # starts at capital
    assert (bh > 0).all()

def test_wheel_stats_on_real_fixture():
    res, _, cfg = _run()
    s = wheel_stats(res.trades, cfg)
    assert s["n_puts_sold"] == 2 and s["n_take_profits"] == 2
    assert s["n_assignments"] == 0 and s["n_called_away"] == 0
    # 4 option legs x 2 contracts x $0.65
    assert s["commission_paid"] == pytest.approx(4 * 2 * 0.65)
    assert s["premium_collected"] > s["premium_paid_to_close"] > 0
    assert s["assignment_rate"] == 0.0

def test_wheel_report_metrics_finite_and_benchmarked():
    res, ch, cfg = _run()
    rep = wheel_report(res, ch, cfg)
    for k in ("total_return", "cagr", "sharpe", "max_drawdown"):
        assert k in rep.metrics and pd.notna(rep.metrics[k])
    assert set(rep.benchmark) >= {"cagr", "sharpe", "max_drawdown"}
    assert 2024 in rep.yearly_return.index
    assert rep.periods_per_year == pytest.approx(252, abs=8)   # daily wheel equity
    # recent_start (2021) predates the fixture's 2024 span, so it clamps to the
    # fixture start and recent == full history — the collapse flag must say so.
    assert set(rep.benchmark_recent.keys()) == set(rep.benchmark.keys())
    assert rep.recent_is_full is True
