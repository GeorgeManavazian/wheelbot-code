"""Chop-quality gates A/B (2026-07-18): does requiring flat-on-both-timescales
(50/200 <=3% AND 9/20 <=1%) help the chop-scanner wheel over 9y?

Live frozen config (0.30 put / 0.50 call / 60% TP / DTE 11 / basis), EOD, on the
9-ticker in-sample dev universe (the only names with 9y of option chains).
Arms: N in {1,5} x gates {OFF, ON}. IN-SAMPLE + thresholds fit to a different
recent batch -- read as a sanity check, not proof.

Run: PYTHONPATH=. .venv/bin/python scripts/run_chop_gate_ab.py
"""
import pandas as pd
from pathlib import Path
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.options.portfolio import run_portfolio_wheel, ROTATION_TIE_ORDER
from src.engine_v2.options.report import buy_hold_curve
from src.engine_v2.backtest import metrics_simple as m
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

UNIVERSE = list(ROTATION_TIE_ORDER)
LIVE = dict(put_delta=0.30, call_delta=0.50, target_dte=11,
            take_profit_pct=0.60, starting_capital=100_000.0, call_min_strike="basis")
# gate variants to A/B
ARMS = {
    "ungated":            {},
    "symmetric fast+struct": dict(chop_max_ma_spread=0.03, chop_max_fast_spread=0.01),
    "down-only fast":     dict(chop_max_fast_fall=0.01),
    "down-only + struct": dict(chop_max_fast_fall=0.01, chop_max_ma_spread=0.03),
}


def perf(eq):
    ppy = m.infer_periods_per_year(eq.index)
    rets = eq.pct_change().fillna(0.0)
    return (float(eq.iloc[-1] / eq.iloc[0] - 1), m.cagr(eq, ppy),
            m.sharpe(rets, ppy), m.max_drawdown(eq))


def line(name, port):
    eq = port.equity
    tot, cagr, sh, dd = perf(eq)
    return (f"{name:<28} {eq.iloc[-1]-100_000:>10,.0f} {tot:>8.1%} {cagr:>7.1%} "
            f"{sh:>7.2f} {dd:>7.1%} {port.n_campaigns_opened:>6} {port.days_flat:>6}")


def main():
    chains = {t: pd.read_parquet(chain_path(t)) for t in UNIVERSE}
    states = {t: regime_series(closes_for(t)) for t in UNIVERSE}
    L = ["Chop-quality gates A/B — live config (0.30/0.50/60%/DTE11/basis), EOD, 9-ticker in-sample.",
         f"universe: {UNIVERSE}",
         f"{'arm':<28} {'P&L':>10} {'total':>8} {'CAGR':>7} {'Sharpe':>7} {'maxDD':>7} {'camps':>6} {'flat':>6}"]
    for n in (1, 5):
        for name, gates in ARMS.items():
            port = run_portfolio_wheel(chains, WheelConfig(ticker="SPY", **LIVE, **gates),
                                       states, selector="chop", n_slots=n)
            L.append(line(f"N={n} {name}", port))
        L.append("")
    bh = buy_hold_curve(chains["SPY"], 100_000.0)
    tot, cagr, sh, dd = perf(bh)
    L.append(f"{'buy-hold SPY':<22} {bh.iloc[-1]-100_000:>10,.0f} {tot:>8.1%} {cagr:>7.1%} {sh:>7.2f} {dd:>7.1%}")
    txt = "\n".join(L)
    Path("data/options/reports").mkdir(parents=True, exist_ok=True)
    Path("data/options/reports/chop_gate_ab.txt").write_text(txt)
    print(txt)


if __name__ == "__main__":
    main()
