"""Defense execution audit: independent double-entry check that the wheel's
stop-loss and roll mechanics fired exactly when the rules say they should —
no misses, no false fires — on real chains.

This deliberately RE-DERIVES the decision rules from raw data instead of
importing the engine's decision code: the engine writes the ledger, this
script recomputes it from the source documents. Agreement is the proof.

Usage:  .venv/bin/python -m scripts.audit_defense_execution [TICKER ...]
        .venv/bin/python -m scripts.audit_defense_execution --hourly [TICKER ...]
        (default: SPY GDX SLV XOP — the in-sample set; never run this on the
        pre-registered unseen basket tickers before the basket run. --hourly
        audits the intraday-TP path against the raw hourly bar parquets and
        skips tickers whose hourly file is not on disk.)

Exit 0 = every held day accounted for, zero mismatches. Non-zero otherwise.
"""
import os
import sys
import pandas as pd

from src.engine_v2.options.wheel import (WheelConfig, run_wheel,
                                         MAX_ROLLS_PER_CAMPAIGN)
from src.engine_v2.options.intraday import run_wheel_intraday
from src.engine_v2.options.select import select_roll_contract, option_mark
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for


# Gate rules RE-DERIVED locally, deliberately NOT imported from wheel.py — the
# audit must not verify the engine with the engine's own functions. A boolean
# mask instead of searchsorted; a bug in either implementation breaks the
# agreement instead of hiding inside it.
def _unpaid(trend, vol):
    return trend == "downtrend" and vol in ("calm", "normal")

def _asof(states, d, max_stale_days=14):
    prior = states.index[states.index < pd.Timestamp(d)]
    if len(prior) == 0:
        return ("unknown", "unknown")
    last = prior[-1]
    if (pd.Timestamp(d) - last).days > max_stale_days:
        return ("unknown", "unknown")
    row = states.loc[last]
    return (row["trend"], row["vol"])

BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0)
VARIANTS = {
    "roll-tested": {"roll_tested_puts": True},
    "put-stop-3x": {"put_stop_mult": 3.0},
    "roll+stop":   {"roll_tested_puts": True, "put_stop_mult": 3.0},
    # regime gates (spec 2026-07-14): re-derive the gate conditions from the
    # same closes the runner uses and assert the ledger agrees.
    "entry-gate":  {"regime_entry_gate": True},
    "roll+gate":   {"roll_tested_puts": True, "regime_roll_gate": True},
    "stop+gate":   {"put_stop_mult": 3.0, "regime_stop_gate": True},
    "all-gates":   {"roll_tested_puts": True, "put_stop_mult": 3.0,
                    "regime_entry_gate": True, "regime_roll_gate": True,
                    "regime_stop_gate": True},
    # siege exit (spec 2026-07-14): uncovered unpaid-decline days re-derived
    # from ledger share/coverage periods + the local state rules.
    "basis+siege": {"call_min_strike": "basis", "regime_siege_exit": True},
    "siege+egate": {"call_min_strike": "basis", "regime_siege_exit": True,
                    "regime_entry_gate": True},
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


_CHAINS, _STATES = {}, {}

def audit(ticker, name, overrides):
    if ticker not in _CHAINS:
        _CHAINS[ticker] = pd.read_parquet(
            f"data/options/{ticker.lower()}_greeks_eod_all.parquet")
    ch = _CHAINS[ticker]
    cfg = WheelConfig(ticker=ticker, **BASE, **overrides)
    states = None
    if cfg.any_regime_gate:
        if ticker not in _STATES:
            _STATES[ticker] = regime_series(closes_for(ticker))
        states = _STATES[ticker]
    res = run_wheel(ch, cfg, regime_states=states)
    und = ch.groupby("date")["underlying"].first()
    by_date = {pd.Timestamp(k): g for k, g in ch.groupby("date")}
    actual = {}
    for t in res.trades:
        actual.setdefault((pd.Timestamp(t.date).normalize(), t.action), []).append(t)

    mismatches, checked_days, fired = [], 0, {"roll": 0, "stop": 0}
    expected_denials = set()   # would-execute roll days blocked by the gate
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
            # rule 2: roll (tested put, mid-life, cap, credit-only). The gate is
            # consulted at the WOULD-EXECUTE moment (destination + credit pass),
            # matching the engine's would-act rule: a gated would-execute day
            # expects a roll_denied event and no ROLL_CLOSE.
            elif (cfg.roll_tested_puts and c.right == "P" and d < c.expiry
                    and spot <= c.strike and rolls_so_far < MAX_ROLLS_PER_CAMPAIGN
                    and mark is not None):
                new_c = select_roll_contract(day_chain, d, "P", c.strike,
                                             c.expiry, cfg.target_dte, cfg.ticker)
                nm = option_mark(day_chain, d, new_c) if new_c is not None else None
                if nm is not None and (nm.bid * 100 * n - cfg.commission_per_contract * n
                        ) >= (mark.ask * 100 * n + cfg.commission_per_contract * n):
                    if cfg.regime_roll_gate and _unpaid(*_asof(states, d)):
                        expected_denials.add(pd.Timestamp(d).normalize())
                    else:
                        expected = "ROLL_CLOSE"
            # rule 3: stop (puts only, mid-life, EOD ask >= mult x credit,
            # not suppressed by the stop gate in stressed vol)
            if (expected is None and cfg.put_stop_mult is not None
                    and c.right == "P" and d < c.expiry and mark is not None
                    and mark.ask >= cfg.put_stop_mult * credit
                    and not (cfg.regime_stop_gate
                             and _asof(states, d)[1] == "stressed")):
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

    # gate checks (spec 2026-07-14): entries never on gated days; every logged
    # gate event re-derives from the same closes series (via the LOCAL rules).
    if cfg.regime_entry_gate:
        for t in res.trades:
            if t.action == "SELL_PUT" and _unpaid(*_asof(states, t.date)):
                mismatches.append(f"{ticker}/{name} {pd.Timestamp(t.date).date()} "
                                  f"SELL_PUT on an entry-gated (unpaid decline) day")
    for (d, kind, c) in (res.gate_events or []):
        g = _asof(states, d)
        if kind in ("entry_gated", "roll_denied_by_gate") and not _unpaid(*g):
            mismatches.append(f"{ticker}/{name} {pd.Timestamp(d).date()} {kind} "
                              f"logged but state {g} is not unpaid decline")
        if kind == "stop_suppressed_by_gate" and g[1] != "stressed":
            mismatches.append(f"{ticker}/{name} {pd.Timestamp(d).date()} {kind} "
                              f"logged but vol state is {g[1]!r}, not stressed")
        fired["gate"] = fired.get("gate", 0) + 1
    # siege exit: re-derive share-holding periods and call coverage from the
    # ledger, then require: the FIRST uncovered unpaid-decline day of every
    # period is exactly where the ledger exits — and a period the ledger holds
    # to the end contains NO such day. Double-entry, both directions.
    if cfg.regime_siege_exit:
        chain_days = list(und.index)
        call_legs = [(pd.Timestamp(l["opened"]).normalize(),
                      pd.Timestamp(l["closed"]).normalize() if l["closed"] is not None else None)
                     for l in positions_from_trades(res.trades)
                     if l["contract"].right == "C"]
        periods = []   # (start, end_day_or_None, ended_by)
        start = None
        for t in res.trades:
            d0 = pd.Timestamp(t.date).normalize()
            if t.action == "ASSIGNED":
                start = d0
            elif t.action in ("CALLED_AWAY", "SIEGE_EXIT") and start is not None:
                periods.append((start, d0, t.action))
                start = None
        if start is not None:
            periods.append((start, None, "OPEN_AT_END"))
        for (p0, p1, ended_by) in periods:
            # uncovered day d: shares held at the entry step's end, no call leg
            # spanning d. CALLED_AWAY day itself: shares already gone (expiry
            # resolution precedes the entry step); SIEGE_EXIT day IS uncovered.
            if p1 is None:
                days = [d for d in chain_days if d >= p0]
            elif ended_by == "SIEGE_EXIT":
                days = [d for d in chain_days if p0 <= d <= p1]
            else:
                days = [d for d in chain_days if p0 <= d < p1]
            derived = None
            for d in days:
                covered = any(o <= d and (c is None or c > d) for (o, c) in call_legs)
                if covered:
                    continue
                if _unpaid(*_asof(states, d)):
                    derived = d
                    break
            actual = p1 if ended_by == "SIEGE_EXIT" else None
            if derived != actual:
                mismatches.append(f"{ticker}/{name} share period {p0.date()}→"
                                  f"{p1.date() if p1 is not None else 'open'}: derived siege exit "
                                  f"{derived.date() if derived is not None else 'none'}, "
                                  f"ledger has {actual.date() if actual is not None else 'none'}")
            elif actual is not None:
                fired["gate"] = fired.get("gate", 0) + 1
    # double-entry on roll denials: the ledger's denial events and the audit's
    # independently re-derived would-execute-but-gated days must be identical.
    if cfg.regime_roll_gate:
        got_denials = {pd.Timestamp(d).normalize()
                       for (d, kind, c) in (res.gate_events or [])
                       if kind == "roll_denied_by_gate"}
        for d in sorted(expected_denials - got_denials):
            mismatches.append(f"{ticker}/{name} {d.date()} roll denial expected "
                              f"(would-execute, unpaid decline) but not logged")
        for d in sorted(got_denials - expected_denials):
            mismatches.append(f"{ticker}/{name} {d.date()} roll denial logged "
                              f"but audit says the roll could not have executed")
    return checked_days, fired, mismatches


# ---------------------------------------------------------------------------
# Hourly (intraday-TP) audit: the plain wheel with hourly fills, re-derived
# from the RAW bar parquet with independent code (own validity filter, own
# bar walk) — not via intraday_marks. For every ledger leg the audit derives
# the first termination event (intraday TP fill / EOD TP / expiry) and its
# exact timestamp + price, then requires the ledger to agree.
# ---------------------------------------------------------------------------

def _load_bars_independent(ticker):
    """Raw hourly bars -> {(expiry, strike, right): ordered [timestamp, close]}.
    Validity rule re-stated locally: a bar with no trade (volume 0) or a zero
    close is not a price."""
    path = f"data/options/{ticker.lower()}_ohlc_1h_all.parquet"
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    df = df[(df["volume"] > 0) & (df["close"] > 0)]
    return {(pd.Timestamp(e), float(s), r):
            g[["timestamp", "close"]].sort_values("timestamp").reset_index(drop=True)
            for (e, s, r), g in df.groupby(["expiry", "strike", "right"])}


def audit_hourly(ticker, start="2020-01-01"):
    ch = pd.read_parquet(f"data/options/{ticker.lower()}_greeks_eod_all.parquet")
    ch["date"] = pd.to_datetime(ch["date"])
    ch = ch[ch["date"] >= pd.Timestamp(start)].reset_index(drop=True)
    bars_by_contract = _load_bars_independent(ticker)
    if bars_by_contract is None:
        return None
    ih = pd.read_parquet(f"data/options/{ticker.lower()}_ohlc_1h_all.parquet")
    ih = ih[ih["timestamp"] >= pd.Timestamp(start)].reset_index(drop=True)
    cfg = WheelConfig(ticker=ticker, **BASE)
    res = run_wheel_intraday(ch, cfg, ih)

    und = ch.groupby("date")["underlying"].first()
    by_date = {pd.Timestamp(k): g for k, g in ch.groupby("date")}
    chain_days = sorted(und.index)
    thresh_mult = 1 - cfg.take_profit_pct

    mismatches, checked_days, verified = [], 0, {"intraday_tp": 0, "eod_tp": 0,
                                                 "expiry": 0}
    for leg in positions_from_trades(res.trades):
        c, n, credit = leg["contract"], leg["n"], leg["credit"]
        key = (pd.Timestamp(c.expiry), float(c.strike), c.right)
        opened = pd.Timestamp(leg["opened"]).normalize()
        thresh = thresh_mult * credit
        cbars = bars_by_contract.get(key)

        expected = None   # (kind, when, price|None)
        for d in chain_days:
            if not (opened < d):
                continue
            checked_days += 1
            if d < pd.Timestamp(c.expiry):
                day = (cbars[cbars["timestamp"].dt.normalize() == d]
                       .reset_index(drop=True)) if cbars is not None else None
                hit = None
                if day is not None and len(day) > 1:
                    for i in range(len(day) - 1):
                        if day.iloc[i]["close"] <= thresh:
                            hit = (day.iloc[i + 1]["timestamp"],
                                   float(day.iloc[i + 1]["close"]))
                            break
                if hit is not None:
                    expected = ("INTRADAY_TP", hit[0], hit[1])
                    break
                mark = option_mark(by_date[d], d, c)
                if mark is not None and mark.ask <= thresh:
                    expected = ("EOD_TP", d, float(mark.ask))
                    break
            else:
                expected = ("EXPIRY", d, None)
                break

        actual_when = leg["closed"]
        actual_action = leg["close_action"]
        loc = f"{ticker}/plain-hourly {c.strike}{c.right} exp {pd.Timestamp(c.expiry).date()}"
        if expected is None:
            if actual_action != "OPEN_AT_END":
                mismatches.append(f"{loc}: no trigger derived but ledger closed "
                                  f"via {actual_action} at {actual_when}")
            continue
        kind, when, price = expected
        if kind in ("INTRADAY_TP", "EOD_TP"):
            if actual_action not in ("CLOSE_PUT", "CLOSE_CALL"):
                mismatches.append(f"{loc}: derived {kind} at {when} but ledger "
                                  f"shows {actual_action} at {actual_when}")
                continue
            if pd.Timestamp(actual_when) != pd.Timestamp(when):
                mismatches.append(f"{loc}: derived {kind} fill at {when} but "
                                  f"ledger closed at {actual_when}")
                continue
            close_tr = [t for t in res.trades
                        if t.action == actual_action and t.contract == c
                        and pd.Timestamp(t.date) == pd.Timestamp(when)]
            if not close_tr or abs(close_tr[0].price_per_contract - price) > 1e-9:
                got = close_tr[0].price_per_contract if close_tr else "missing"
                mismatches.append(f"{loc}: derived fill price {price} at {when}, "
                                  f"ledger has {got}")
                continue
            verified["intraday_tp" if kind == "INTRADAY_TP" else "eod_tp"] += 1
        else:  # EXPIRY
            if actual_action not in ("PUT_EXPIRED", "CALL_EXPIRED", "ASSIGNED",
                                     "CALLED_AWAY"):
                mismatches.append(f"{loc}: derived expiry resolution at {when} "
                                  f"but ledger shows {actual_action} at {actual_when}")
                continue
            if pd.Timestamp(actual_when).normalize() != pd.Timestamp(when).normalize():
                mismatches.append(f"{loc}: derived expiry resolution {when} but "
                                  f"ledger resolved at {actual_when}")
                continue
            verified["expiry"] += 1
    return checked_days, verified, mismatches


def main_hourly(tickers):
    total_mm, lines = [], []
    for t in tickers:
        out = audit_hourly(t)
        if out is None:
            lines.append(f"{t:<4} plain-hourly  SKIPPED (no hourly parquet on disk)")
            continue
        days, ver, mm = out
        lines.append(f"{t:<4} plain-hourly  held-days checked {days:>6}  "
                     f"intraday TPs verified {ver['intraday_tp']:>4}  "
                     f"EOD TPs {ver['eod_tp']:>4}  expiries {ver['expiry']:>4}  "
                     f"mismatches {len(mm)}")
        total_mm.extend(mm)
    print("\n".join(lines))
    if total_mm:
        print(f"\n{len(total_mm)} MISMATCHES:")
        print("\n".join(total_mm[:40]))
        sys.exit(1)
    print("\nHOURLY EXECUTION VERIFIED: every derived fill matches the ledger, "
          "timestamp and price.")


def main():
    if "--hourly" in sys.argv:
        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        main_hourly([t.upper() for t in args] or ["GDX", "SLV", "XOP"])
        return
    tickers = [t.upper() for t in sys.argv[1:]] or ["SPY", "GDX", "SLV", "XOP"]
    total_mm, lines = [], []
    for t in tickers:
        for name, ov in VARIANTS.items():
            days, fired, mm = audit(t, name, ov)
            lines.append(f"{t:<4} {name:<12} held-days checked {days:>6}  "
                         f"rolls verified {fired['roll']:>4}  "
                         f"stops verified {fired['stop']:>4}  "
                         f"gate events verified {fired.get('gate', 0):>5}  "
                         f"mismatches {len(mm)}")
            total_mm.extend(mm)
    print("\n".join(lines))
    if total_mm:
        print(f"\n{len(total_mm)} MISMATCHES:")
        print("\n".join(total_mm[:40]))
        sys.exit(1)
    print("\nEXECUTION VERIFIED: every trigger fired, nothing fired without a trigger.")


if __name__ == "__main__":
    main()
