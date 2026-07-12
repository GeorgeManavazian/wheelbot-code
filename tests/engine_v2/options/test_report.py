import os
import pandas as pd
import pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel
from src.engine_v2.options.report import buy_hold_curve, spy_curve, wheel_stats, wheel_report

FIX = "fixtures/spy_wheel_cycle.parquet"
pytestmark = pytest.mark.skipif(not os.path.exists(FIX), reason="wheel cycle fixture not built")

def _run():
    ch = pd.read_parquet(FIX)
    cfg = WheelConfig(target_dte=40, put_delta=0.30, call_delta=0.30)
    return run_wheel(ch, cfg), ch, cfg

def test_buy_hold_curve_shape():
    _, ch, _ = _run()
    bh = buy_hold_curve(ch, 100_000)
    assert bh.iloc[0] == pytest.approx(100_000)          # starts at capital
    assert (bh > 0).all()
    assert bh.name == "buy_hold"

def test_spy_curve_none_when_file_missing():
    _, ch, _ = _run()
    idx = ch.groupby("date")["underlying"].first().index
    assert spy_curve(100_000, idx, path="nonexistent.parquet") is None

def test_wheel_stats_on_real_fixture():
    res, _, cfg = _run()
    s = wheel_stats(res, cfg)
    assert s["n_puts_sold"] == 2 and s["n_take_profits"] == 2
    assert s["n_assignments"] == 0 and s["n_called_away"] == 0
    # 4 option legs x 2 contracts x $0.65
    assert s["commission_paid"] == pytest.approx(4 * 2 * 0.65)
    assert s["premium_collected"] > s["premium_paid_to_close"] > 0
    assert s["assignment_rate"] == 0.0

def test_wheel_report_metrics_finite_and_benchmarked():
    res, ch, cfg = _run()
    rep = wheel_report(res, ch, cfg, spy_path="nonexistent.parquet")
    for k in ("total_return", "cagr", "sharpe", "max_drawdown"):
        assert k in rep.metrics and pd.notna(rep.metrics[k])
    assert set(rep.benchmark_underlying) >= {"cagr", "sharpe", "max_drawdown"}
    assert 2024 in rep.yearly_return.index
    assert rep.periods_per_year == pytest.approx(252, abs=8)   # daily wheel equity
    # recent_start (2021) predates the fixture's 2024 span, so it clamps to the
    # fixture start and recent == full history — the collapse flag must say so.
    assert set(rep.benchmark_underlying_recent.keys()) == set(rep.benchmark_underlying.keys())
    assert rep.recent_is_full is True

def test_benchmark_underlying_is_this_chain():
    res, ch, cfg = _run()
    rep = wheel_report(res, ch, cfg, spy_path="nonexistent.parquet")
    assert rep.benchmark_spy is None
    assert rep.benchmark_underlying["total_return"] == pytest.approx(
        float(ch.groupby("date")["underlying"].first().iloc[-1]
              / ch.groupby("date")["underlying"].first().iloc[0] - 1), rel=1e-6)

def test_spy_benchmark_loads_real_spy(tmp_path):
    res, ch, cfg = _run()
    spy = ch.copy(); spy["underlying"] = spy["underlying"] * 2  # distinct series
    p = tmp_path / "spy.parquet"; spy.to_parquet(p)
    rep = wheel_report(res, ch, cfg, spy_path=str(p))
    assert rep.benchmark_spy is not None

def test_stats_have_dte_distribution_and_flat_days():
    res, ch, cfg = _run()
    rep = wheel_report(res, ch, cfg, spy_path="nonexistent.parquet")
    assert rep.stats["n_days_flat"] == res.days_flat
    assert 0.0 <= rep.stats["pct_days_flat"] <= 1.0
    assert rep.stats["realized_dte"].sum() == rep.stats["n_puts_sold"] + rep.stats["n_calls_sold"]
