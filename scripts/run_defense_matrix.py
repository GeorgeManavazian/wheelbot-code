"""Defense matrix (spec amendment 2026-07-12b): plain wheel + 4 pre-registered
defense variants, frozen base config, every ticker with an EOD chain on disk.
All results reported side by side — no best-cell selection.

Run: PYTHONPATH=. .venv/bin/python scripts/run_defense_matrix.py [TICKER ...]
"""
import sys
import pandas as pd
from pathlib import Path
from src.engine_v2.options.data import available_tickers, chain_path
from src.engine_v2.options.wheel import run_wheel, WheelConfig
from src.engine_v2.options.report import wheel_report, position_log

BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0)

VARIANTS = {
    "plain":       {},
    "call>=basis": {"call_min_strike": "basis"},
    "roll-tested": {"roll_tested_puts": True},
    "liquidate":   {"liquidate_assignment": True},
    "put-stop-3x": {"put_stop_mult": 3.0},
}

def leg_split(res, cfg):
    log = position_log(res, cfg).dropna(subset=["realized_pnl"])
    shares = log[log.instrument == "SHARES"]["realized_pnl"].sum()
    opts = log[log.instrument != "SHARES"]["realized_pnl"].sum()
    return opts, shares

SEEN = ["SPY", "GDX", "SLV", "XOP"]
UNSEEN = {"XBI", "EEM", "EWZ", "TLT", "ARKK"}

def main():
    # default is the SEEN set, never available_tickers(): once the basket pull
    # finished, "every chain on disk" silently includes the pre-registered
    # unseen tickers (this exact leak happened 2026-07-14 — see the regime-gates
    # spec's contamination amendment). Unseen requires the explicit flag.
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tickers = [t.upper() for t in args] or SEEN
    # ALLOW-list: anything outside the seen set is refused (deny-lists fail
    # open on typos/new tickers — review 2026-07-14).
    blocked = [t for t in tickers if t not in SEEN]
    if blocked and "--after-basket-run" not in sys.argv:
        sys.exit(f"REFUSED: {blocked} are outside the seen set {SEEN}. "
                 f"Run the basket first, then pass --after-basket-run.")
    outd = Path("data/options/reports"); outd.mkdir(parents=True, exist_ok=True)
    lines = []
    for t in tickers:
        ch = pd.read_parquet(chain_path(t))
        lines.append(f"\n=== {t}  ({ch['date'].min().date()} → {ch['date'].max().date()}) ===")
        lines.append(f"{'variant':<12} {'P&L':>10} {'Sharpe':>7} {'maxDD':>8} "
                     f"{'opts P&L':>10} {'shares P&L':>11} {'assign':>7} {'trades':>7}")
        for name, overrides in VARIANTS.items():
            cfg = WheelConfig(ticker=t, **BASE, **overrides)
            res = run_wheel(ch, cfg)
            rep = wheel_report(res, ch, cfg)
            opts, shares = leg_split(res, cfg)
            s = rep.stats
            lines.append(
                f"{name:<12} {res.equity.iloc[-1] - cfg.starting_capital:>10,.0f} "
                f"{rep.metrics['sharpe']:>7.2f} {rep.metrics['max_drawdown']:>8.1%} "
                f"{opts:>10,.0f} {shares:>11,.0f} "
                f"{s['n_assignments']:>7} {len(res.trades):>7}")
        bh = (ch.groupby('date')['underlying'].first())
        lines.append(f"{'buy-hold ' + t:<12} {100_000 * (bh.iloc[-1] / bh.iloc[0] - 1):>10,.0f}")
    txt = "\n".join(lines)
    (outd / "defense_matrix.txt").write_text(txt)
    print(txt)

if __name__ == "__main__":
    main()
