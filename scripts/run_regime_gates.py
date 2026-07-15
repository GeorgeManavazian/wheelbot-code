"""Regime-gates A/B (pre-registered, spec 2026-07-14): three paired arms at the
frozen basket config. Baseline vs gated, per ticker, raw — no best-cell selection.

DISCIPLINE: default tickers are the SEEN set. The all-10 run (incl. unseen
XBI EEM EWZ TLT ARKK) happens ONLY after the pre-registered basket run;
requesting an unseen ticker requires --after-basket-run.

Run: PYTHONPATH=. .venv/bin/python scripts/run_regime_gates.py [TICKER ...]
"""
import sys
import pandas as pd
from pathlib import Path
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.wheel import run_wheel, WheelConfig
from src.engine_v2.options.report import wheel_report, buy_hold_curve
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0)
SEEN = ["SPY", "GDX", "SLV", "XOP"]
UNSEEN = {"XBI", "EEM", "EWZ", "TLT", "ARKK"}
ARMS = [  # (arm, baseline overrides, gated overrides)
    ("E", {}, {"regime_entry_gate": True}),
    ("R", {"roll_tested_puts": True},
          {"roll_tested_puts": True, "regime_roll_gate": True}),
    ("S", {"put_stop_mult": 3.0},
          {"put_stop_mult": 3.0, "regime_stop_gate": True}),
]

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tickers = [t.upper() for t in args] or SEEN
    # ALLOW-list: anything outside the seen set is refused (deny-lists fail
    # open on typos/new tickers — review 2026-07-14).
    blocked = [t for t in tickers if t not in SEEN]
    if blocked and "--after-basket-run" not in sys.argv:
        sys.exit(f"REFUSED: {blocked} are outside the seen set {SEEN}. "
                 f"Run the basket first, then pass --after-basket-run.")
    lines = ["Regime gates A/B — frozen basket config, raw, no selection.",
             "Intraday fills not used here (EOD only). Gated-entry counterfactual",
             "is not modeled; the baseline arm IS the counterfactual."]
    for t in tickers:
        ch = pd.read_parquet(chain_path(t))
        states = regime_series(closes_for(t))
        lines.append(f"\n=== {t}  ({ch['date'].min().date()} → {ch['date'].max().date()}) ===")
        lines.append(f"{'arm':<22} {'P&L':>10} {'Sharpe':>7} {'maxDD':>8} "
                     f"{'gated/denied/suppr':>19} {'unknown':>8}")
        for arm, base_ov, gate_ov in ARMS:
            for label, ov in ((f"{arm}: baseline", base_ov), (f"{arm}: gated", gate_ov)):
                cfg = WheelConfig(ticker=t, **BASE, **ov)
                res = run_wheel(ch, cfg, regime_states=states)
                rep = wheel_report(res, ch, cfg)
                g = rep.gates or {}
                fired = (f"{g.get('days_entry_gated', 0)}/{g.get('n_rolls_denied', 0)}"
                         f"/{g.get('n_stops_suppressed', 0)}")
                lines.append(f"{label:<22} {res.equity.iloc[-1] - cfg.starting_capital:>10,.0f} "
                             f"{rep.metrics['sharpe']:>7.2f} {rep.metrics['max_drawdown']:>8.1%} "
                             f"{fired:>19} {g.get('n_state_unknown', 0):>8}")
        cap = BASE["starting_capital"]
        bh = buy_hold_curve(ch, cap)
        lines.append(f"{'buy-hold ' + t:<22} {bh.iloc[-1] - cap:>10,.0f}")
    txt = "\n".join(lines)
    outd = Path("data/options/reports"); outd.mkdir(parents=True, exist_ok=True)
    # the canonical pre-registered artifact is the full seen-set run; any other
    # ticker subset writes a suffixed file so a spot-check can never clobber it.
    fname = ("regime_gates.txt" if tickers == SEEN
             else f"regime_gates_{'_'.join(t.lower() for t in tickers)}.txt")
    (outd / fname).write_text(txt)
    print(txt)

if __name__ == "__main__":
    main()
