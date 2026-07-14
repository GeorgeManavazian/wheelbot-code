"""Defense execution audit: independent double-entry check that the wheel's
stop-loss and roll mechanics fired exactly when the rules say they should —
no misses, no false fires — on real chains.

This deliberately RE-DERIVES the decision rules from raw data instead of
importing the engine's decision code: the engine writes the ledger, this
script recomputes it from the source documents. Agreement is the proof.

Usage:  .venv/bin/python -m scripts.audit_defense_execution [TICKER ...]
        (default: SPY GDX SLV XOP — the in-sample set; never run this on the
        pre-registered unseen basket tickers before the basket run.)

Exit 0 = every held day accounted for, zero mismatches. Non-zero otherwise.
"""
import sys
import pandas as pd

from src.engine_v2.options.wheel import (WheelConfig, run_wheel,
                                         MAX_ROLLS_PER_CAMPAIGN)
from src.engine_v2.options.select import select_roll_contract, option_mark

BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0)
VARIANTS = {
    "roll-tested": {"roll_tested_puts": True},
    "put-stop-3x": {"put_stop_mult": 3.0},
    "roll+stop":   {"roll_tested_puts": True, "put_stop_mult": 3.0},
}
MANAGE = {"CLOSE_PUT", "CLOSE_CALL", "ROLL_CLOSE", "STOP_CLOSE"}
TERMINAL = MANAGE | {"PUT_EXPIRED", "CALL_EXPIRED", "ASSIGNED", "CALLED_AWAY"}


def positions_from_trades(trades):
    """Reconstruct every held option leg from the ledger: (contract, n, credit,
    open_date, close_date, close_action, rolls_before)."""
    legs, open_leg = [], None
    rolls_in_campaign = {}
    for t in trades:
        if t.action in ("SELL_PUT", "SELL_CALL", "ROLL_OPEN"):
            nrolls = rolls_in_campaign.get(t.campaign_id, 0)
            open_leg = dict(contract=t.contract, n=t.contracts,
                            credit=t.price_per_contract, opened=t.date,
                            campaign=t.campaign_id, rolls_before=nrolls)
        elif t.action in TERMINAL and open_leg is not None:
            open_leg["closed"] = t.date
            open_leg["close_action"] = t.action
            legs.append(open_leg)
            if t.action == "ROLL_CLOSE":
                rolls_in_campaign[t.campaign_id] = \
                    rolls_in_campaign.get(t.campaign_id, 0) + 1
            open_leg = None
    if open_leg is not None:
        open_leg["closed"] = None
        open_leg["close_action"] = "OPEN_AT_END"
        legs.append(open_leg)
    return legs


def audit(ticker, name, overrides):
    ch = pd.read_parquet(f"data/options/{ticker.lower()}_greeks_eod_all.parquet")
    cfg = WheelConfig(ticker=ticker, **BASE, **overrides)
    res = run_wheel(ch, cfg)
    und = ch.groupby("date")["underlying"].first()
    by_date = {pd.Timestamp(k): g for k, g in ch.groupby("date")}
    actual = {}
    for t in res.trades:
        actual.setdefault((pd.Timestamp(t.date).normalize(), t.action), []).append(t)

    mismatches, checked_days, fired = [], 0, {"roll": 0, "stop": 0}
    for leg in positions_from_trades(res.trades):
        c, n, credit = leg["contract"], leg["n"], leg["credit"]
        end = leg["closed"] if leg["closed"] is not None else und.index[-1]
        days = [d for d in und.index if leg["opened"] < d <= end]
        rolls_so_far = leg["rolls_before"]
        for d in days:
            if d > end:
                break
            checked_days += 1
            day_chain = by_date[pd.Timestamp(d)]
            spot = float(und[d])
            mark = option_mark(day_chain, d, c)
            expected = None

            # rule 1: TP (EOD; these audit runs use no intraday)
            if (cfg.take_profit_pct is not None and cfg.take_profit_pct < 1.0
                    and d < c.expiry and mark is not None
                    and mark.ask <= (1 - cfg.take_profit_pct) * credit):
                expected = "CLOSE_PUT" if c.right == "P" else "CLOSE_CALL"
            # rule 2: roll (tested put, mid-life, cap, credit-only)
            elif (cfg.roll_tested_puts and c.right == "P" and d < c.expiry
                    and spot <= c.strike and rolls_so_far < MAX_ROLLS_PER_CAMPAIGN
                    and mark is not None):
                new_c = select_roll_contract(day_chain, d, "P", c.strike,
                                             c.expiry, cfg.target_dte, cfg.ticker)
                nm = option_mark(day_chain, d, new_c) if new_c is not None else None
                if nm is not None and (nm.bid * 100 * n - cfg.commission_per_contract * n
                        ) >= (mark.ask * 100 * n + cfg.commission_per_contract * n):
                    expected = "ROLL_CLOSE"
            # rule 3: stop (puts only, mid-life, EOD ask >= mult x credit)
            if (expected is None and cfg.put_stop_mult is not None
                    and c.right == "P" and d < c.expiry and mark is not None
                    and mark.ask >= cfg.put_stop_mult * credit):
                expected = "STOP_CLOSE"
            # rule 4: expiry resolution
            if expected is None and d >= pd.Timestamp(c.expiry):
                expected = "EXPIRY"

            got = [a for (dd, a), ts in actual.items()
                   if dd == d and any(t.contract == c for t in ts)
                   and a in TERMINAL]
            if expected in ("CLOSE_PUT", "CLOSE_CALL", "ROLL_CLOSE", "STOP_CLOSE"):
                if expected not in got:
                    mismatches.append(f"{ticker}/{name} {d.date()} {c.strike}{c.right} "
                                      f"expected {expected}, ledger has {got or 'nothing'}")
                else:
                    if expected == "ROLL_CLOSE":
                        fired["roll"] += 1
                    if expected == "STOP_CLOSE":
                        fired["stop"] += 1
                break  # leg ends or changes on an action day
            if expected == "EXPIRY":
                if not any(a in ("PUT_EXPIRED", "CALL_EXPIRED", "ASSIGNED",
                                 "CALLED_AWAY", "STOP_CLOSE", "ROLL_CLOSE") for a in got):
                    mismatches.append(f"{ticker}/{name} {d.date()} {c.strike}{c.right} "
                                      f"expected expiry resolution, ledger has nothing")
                break
            # expected None: bot must have done NOTHING to this leg today
            if got:
                mismatches.append(f"{ticker}/{name} {d.date()} {c.strike}{c.right} "
                                  f"no rule triggered but ledger shows {got}")
                break
    return checked_days, fired, mismatches


def main():
    tickers = [t.upper() for t in sys.argv[1:]] or ["SPY", "GDX", "SLV", "XOP"]
    total_mm, lines = [], []
    for t in tickers:
        for name, ov in VARIANTS.items():
            days, fired, mm = audit(t, name, ov)
            lines.append(f"{t:<4} {name:<12} held-days checked {days:>6}  "
                         f"rolls verified {fired['roll']:>4}  "
                         f"stops verified {fired['stop']:>4}  mismatches {len(mm)}")
            total_mm.extend(mm)
    print("\n".join(lines))
    if total_mm:
        print(f"\n{len(total_mm)} MISMATCHES:")
        print("\n".join(total_mm[:40]))
        sys.exit(1)
    print("\nEXECUTION VERIFIED: every trigger fired, nothing fired without a trigger.")


if __name__ == "__main__":
    main()
