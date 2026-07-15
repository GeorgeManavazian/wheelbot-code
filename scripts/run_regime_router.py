"""Regime-router A/B (pre-registered, spec 2026-07-14-regime-router-design):
router vs BOTH benchmarks — buy-hold the ticker AND the solo wheel+basis —
per ticker, raw, EOD, frozen config. Seen tickers only.

Run: PYTHONPATH=. .venv/bin/python scripts/run_regime_router.py [TICKER ...]
"""
import sys
import pandas as pd
from pathlib import Path
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.wheel import WheelConfig, run_wheel
from src.engine_v2.options.regime_router import run_regime_router
from src.engine_v2.options.report import buy_hold_curve
from src.engine_v2.backtest import metrics_simple as m
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

SEEN = ["SPY", "GDX", "SLV", "XOP"]
UNSEEN = {"XBI", "EEM", "EWZ", "TLT", "ARKK", "QQQ"}
XOP_CLEAN_START = pd.Timestamp("2020-07-01")
BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0,
            call_min_strike="basis")

def perf(eq):
    ppy = m.infer_periods_per_year(eq.index)
    rets = eq.pct_change().fillna(0.0)
    return (float(eq.iloc[-1] / eq.iloc[0] - 1), m.cagr(eq, ppy),
            m.sharpe(rets, ppy), m.max_drawdown(eq))

def line(name, eq, extra=""):
    tot, cagr, sh, dd = perf(eq)
    return (f"{name:<24} {eq.iloc[-1] - 100_000:>10,.0f} {tot:>8.1%} "
            f"{cagr:>7.1%} {sh:>7.2f} {dd:>7.1%}  {extra}")

def main():
    tickers = [t.upper() for t in sys.argv[1:] if not t.startswith("--")] or SEEN
    blocked = [t for t in tickers if t in UNSEEN]
    if blocked and "--after-basket-run" not in sys.argv:
        sys.exit(f"REFUSED: {blocked} are pre-registered unseen tickers. "
                 f"Run the basket first, then pass --after-basket-run.")
    lines = ["Regime router A/B — frozen config, EOD, raw. Dual benchmarks:",
             "router must beat buy-hold (else just hold) AND solo wheel+basis",
             "(else routing doesn't earn its complexity)."]
    for t in tickers:
        ch = pd.read_parquet(chain_path(t))
        ch["date"] = pd.to_datetime(ch["date"])
        if t == "XOP":
            ch = ch[ch["date"] >= XOP_CLEAN_START].reset_index(drop=True)
        states = regime_series(closes_for(t))
        cfg = WheelConfig(ticker=t, **BASE)

        router = run_regime_router(ch, cfg, states)
        solo = run_wheel(ch, cfg)
        bh = buy_hold_curve(ch, cfg.starting_capital)

        dip = router.days_in_posture
        transitions = sum(1 for a, b in zip(router.route_log, router.route_log[1:])
                          if a[3] != b[3])
        lines.append(f"\n=== {t}  ({ch['date'].min().date()} → {ch['date'].max().date()}) ===")
        lines.append(f"{'arm':<24} {'P&L':>10} {'total':>8} {'CAGR':>7} {'Sharpe':>7} {'maxDD':>7}")
        lines.append(line("ROUTER", router.equity,
                          f"| days T/W/C {dip['TREND']}/{dip['WHEEL']}/{dip['CASH']}"
                          f"  transitions {transitions}  whipsaws {router.whipsaw_pairs}"
                          f"  unknown {sum(1 for w in router.warnings if w[1]=='route_state_unknown')}"))
        lines.append(line("solo wheel+basis", solo.equity))
        lines.append(line(f"buy-hold {t}", bh))
        yr = m.yearly_returns(router.equity)
        lines.append("router per-year: " + "  ".join(f"{y}: {yr[y]:+.1%}" for y in yr.index))
    txt = "\n".join(lines)
    outd = Path("data/options/reports"); outd.mkdir(parents=True, exist_ok=True)
    fname = ("regime_router.txt" if tickers == SEEN
             else f"regime_router_{'_'.join(t.lower() for t in tickers)}.txt")
    (outd / fname).write_text(txt)
    print(txt)

if __name__ == "__main__":
    main()
