import pandas as pd
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.options.regime_router import run_regime_router
from src.engine_v2.options.report import router_report
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for
from src.engine_v2.backtest import metrics_simple as m

BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0, call_min_strike="basis")


def _run(ticker="SPY"):
    ch = pd.read_parquet(chain_path(ticker))
    ch["date"] = pd.to_datetime(ch["date"])
    states = regime_series(closes_for(ticker))
    cfg = WheelConfig(ticker=ticker, **BASE)
    return run_regime_router(ch, cfg, states), ch, cfg


def test_router_report_metrics_match_equity():
    res, ch, cfg = _run()
    rep = router_report(res, ch, cfg)
    ppy = m.infer_periods_per_year(res.equity.index)
    assert rep.metrics["total_return"] == float(res.equity.iloc[-1] / res.equity.iloc[0] - 1)
    assert rep.metrics["max_drawdown"] == m.max_drawdown(res.equity)
    assert abs(rep.metrics["cagr"] - m.cagr(res.equity, ppy)) < 1e-12


def test_router_report_posture_and_fills():
    res, ch, cfg = _run()
    rep = router_report(res, ch, cfg)
    assert rep.posture["days"] == res.days_in_posture
    assert rep.posture["whipsaws"] == res.whipsaw_pairs
    # transitions = count of posture changes across the route_log
    expected_tr = sum(1 for a, b in zip(res.route_log, res.route_log[1:]) if a[3] != b[3])
    assert rep.posture["transitions"] == expected_tr
    assert rep.fills["intraday_tp"] == res.intraday_tp_fills
    assert rep.fills["eod_tp"] == res.eod_tp_fills


def test_router_report_does_not_touch_days_flat():
    # RouterResult has no days_flat; router_report must not read it (the reason
    # wheel_report cannot be reused here).
    res, ch, cfg = _run()
    assert not hasattr(res, "days_flat")
    rep = router_report(res, ch, cfg)   # must not raise
    assert rep.blotter is not None
