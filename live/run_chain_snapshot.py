"""RTH chain-snapshot runner (A16). Data-only, NO order code, writes nothing
but the snapshot file.

  PYTHONPATH=. .venv-live/bin/python live/run_chain_snapshot.py [--force]

The daily decision runs at 17:00 ET, after the options close, where quotes are
3-4x wider than tradeable. This runner executes DURING regular hours (weekdays
15:20-15:55 ET, fired by wheelbot_tick.sh), pulls the same chains the 17:00
run would have pulled, and persists them via live/chain_store.py; run_daily.py
then consumes the snapshot instead of the live post-close endpoint.

Split, not moved: the decision itself must stay post-close, because expiry
settlement and equity marking need the official 16:00 close (portfolio.py's
settle path refuses anything else). The candidate set (held + good-to-rent) is
computed from PRIOR-session data only (_row_before), so the set pulled here is
identical to the one the 17:00 run computes.

Exit codes: 0 = snapshot saved (tick writes the once-per-day marker);
non-zero = no snapshot (tick retries on every remaining tick in the window).
An EMPTY snapshot (no held, nothing good-to-rent) still saves and exits 0 --
"present but empty" must stay distinguishable from "the window failed".
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")

# Inside RTH (quotes live and tradeable), late enough to be near the state the
# 17:00 decision acts on, ending early enough that a ~7-minute pull started on
# the last allowed tick still finishes by the 16:00 close (skeptic F4: the
# original 15:55 close meant a last-tick start finished ~16:02, past it).
SNAP_OPEN, SNAP_CLOSE = 1520, 1550

# A pull that STARTED in-window may legitimately FINISH a few minutes past
# 16:00 on a slow day -- the book it read is the closing book, not the decayed
# post-close ghost the audit measured at 17:00+. Past this grace, saving would
# stamp genuinely post-close quotes as the blessed RTH snapshot (skeptic F4),
# so the save is refused and the day gaps instead of lying.
SAVE_DEADLINE = 1605


def additive_call_rows(primary, wide):
    """A4 splice filter: from a full OTM-call pull, keep ONLY rows strictly
    above the primary chain's per-expiry max CALL strike, for expiries the
    primary already carries call rows for. Purely additive at the top of the
    existing window: it cannot introduce a new expiry into select_contract's
    candidate set, and (call delta decreasing in strike) every added row's
    delta is strictly further from the 0.50 target than an existing row's --
    so the selected contract is bit-identical whenever the floor was already
    reachable. (Provisional splice decision, analyst-proven, 2026-08-01.)"""
    if wide is None or len(wide) == 0:
        return []
    calls = primary[primary["right"] == "C"]
    if len(calls) == 0:
        return []
    tops = calls.groupby("expiry")["strike"].max()
    # A4 skeptic F1: the bit-identical guarantee relies on call delta being
    # monotone decreasing in strike. A garbage-but-plausible delta (~0.49) on
    # a far-OTM spliced row would WIN select_contract's |delta-target| idxmin
    # and silently move the sold strike. Enforce the monotonicity the
    # guarantee needs: a spliced row's |delta| must sit strictly below the
    # primary's minimum call |delta| for that expiry, else the row is dropped
    # (the quote is lying about its own moneyness).
    floors_ = calls.copy()
    floors_["_ad"] = floors_["delta"].abs()
    min_deltas = floors_.groupby("expiry")["_ad"].min()
    out = []
    for _, r in wide[wide["right"] == "C"].iterrows():
        top = tops.get(r["expiry"])
        if top is None or top != top or not float(r["strike"]) > float(top):
            continue
        dmin = min_deltas.get(r["expiry"])
        d = abs(float(r["delta"])) if r["delta"] == r["delta"] else None
        if d is None or dmin is None or dmin != dmin or not d < float(dmin):
            continue
        out.append(r.to_dict())
    return out


def snapshot_window_open(now_et) -> bool:
    """Weekday 15:20-15:50 ET. 17:00 is NOT in the window -- that is the
    defect (A16), not a fallback."""
    if now_et.weekday() >= 5:
        return False
    hm = now_et.hour * 100 + now_et.minute
    return SNAP_OPEN <= hm <= SNAP_CLOSE


def save_still_rth(now_et) -> bool:
    """Re-checked AFTER the pull, before saving -- the start gate alone lets a
    hung or slow pull bless post-close quotes as RTH."""
    return now_et.hour * 100 + now_et.minute <= SAVE_DEADLINE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="pull outside the RTH window (manual/backfill use only; "
                         "the result is exactly the post-close book A16 bans)")
    args = ap.parse_args()

    from live.universe import UNIVERSE
    from live.accounts import all_accounts, account_paths
    from live.state import load_state
    from live.chain_store import save_chain_snapshot
    from live.config import load_run_config
    from live.run_daily import FROZEN, _live_market, zombie_check
    from src.engine_v2.options.wheel import WheelConfig

    now = dt.datetime.now(ET)
    if not args.force and not snapshot_window_open(now):
        # exit non-zero so the tick never writes the done-marker for a refusal
        print(f"chain snapshot REFUSED: {now:%Y-%m-%d %H:%M} ET is outside the "
              f"{SNAP_OPEN}-{SNAP_CLOSE} RTH window (A16). Use --force only if "
              f"you want the post-close book on purpose.")
        return 1

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "schwab"))
    from schwab_client import get_client
    client = get_client()

    obs = pd.Timestamp(now.date())
    held_all, call_floors = set(), {}
    for (cap, n) in all_accounts():
        st = load_state(account_paths(cap, n)["state"])
        if st is None:
            continue
        for p in st.positions:
            held_all.add(p["ticker"])
            # A4: the highest basis floor across accounts per CALL-phase
            # ticker -- the strike the covered-call selection must reach.
            # Per-position try/except (skeptic F2): one hand-corrupted state
            # file must degrade to "no wide pull for that leg", never kill the
            # snapshot for all 25 accounts -- the 17:00 step is per-account
            # isolated and this pass must not be weaker.
            try:
                if (p.get("phase") == "CALL" and p.get("shares", 0) >= 100
                        and p.get("basis") is not None):
                    floor = p["basis"] - p["premium"] / p["shares"]
                    call_floors[p["ticker"]] = max(
                        call_floors.get(p["ticker"], float("-inf")), floor)
            except (TypeError, ZeroDivisionError, KeyError) as e:
                print(f"A4: malformed position for {p.get('ticker')} "
                      f"({type(e).__name__}) -- floor skipped for that leg")

    cfg = WheelConfig(ticker="SPY", starting_capital=100_000.0, **FROZEN)
    market = _live_market(UNIVERSE, held_all, obs, client, cfg.target_dte,
                          None,  # chains=None: this IS the one legitimate RTH live pull
                          cfg.chop_max_ma_spread, cfg.chop_max_fast_spread,
                          cfg.chop_max_fast_fall)

    trunc = getattr(market, "truncated_closes", [])
    if trunc:   # B8: visible, never a failure
        print(f"closes truncated for {len(trunc)} ticker(s): "
              f"{[t for t, _ in trunc[:8]]} -- entry-ineligible, not failures")
    thr = load_run_config()["zombie_threshold"]
    if zombie_check(len(market.skipped_closes), len(UNIVERSE),
                    market.chain_attempts, market.chains_ok, thr):
        print(f"chain snapshot FAILED {obs.date()}: price history "
              f"{len(market.skipped_closes)}/{len(UNIVERSE)} failed, chains "
              f"{market.chains_ok}/{market.chain_attempts} usable (threshold "
              f"{thr:.0%}). Nothing saved; the next tick in the window retries.")
        return 1

    # A4: the 12-strike spot-centred window cannot reach a post-drawdown
    # basis floor. For CALL-phase holdings, pull EVERY listed OTM call and
    # splice the strikes above the window in -- additively only. A failed
    # wide pull degrades to today's behavior (reach unchanged), never fails
    # the snapshot.
    if call_floors:
        from live.data import otm_call_frame
        for tk in sorted(call_floors):
            floor = call_floors[tk]
            base = market._chains.get(tk)
            if base is None or len(base) == 0:
                print(f"A4: {tk} is CALL-phase but has no primary chain -- "
                      f"floor pull skipped; covered call unreachable today")
                continue
            try:
                wide = otm_call_frame(client, tk, cfg.target_dte, obs_date=obs)
            except Exception as e:
                print(f"A4: {tk} OTM-call pull failed ({type(e).__name__}: {e})"
                      f" -- covered-call reach unchanged today")
                continue
            added = market.add_chain_rows(tk, additive_call_rows(base, wide))
            calls = market._chains[tk]
            calls = calls[calls["right"] == "C"]
            top = float(calls["strike"].max()) if len(calls) else float("nan")
            status = "OK" if top >= floor else "FLOOR NOT COVERED"
            print(f"A4: {tk} floor={floor:.2f} top_call={top:.2f} "
                  f"added={added} rows -- {status}")

    done = dt.datetime.now(ET)
    if not args.force and not save_still_rth(done):
        print(f"chain snapshot DISCARDED: pull finished {done:%H:%M} ET, past "
              f"the {SAVE_DEADLINE} grace -- these are post-close quotes and "
              f"must not be blessed as RTH (A16). Nothing saved.")
        return 1

    path = save_chain_snapshot(obs, market._chains, pulled_at=now.isoformat())
    print(f"chain snapshot {obs.date()}: {market.chains_ok}/{market.chain_attempts} "
          f"chains for {len(held_all)} held + good-to-rent -> {path}")
    if market.skipped_chains:
        # Skeptic F3: a sub-threshold chain failure used to freeze that ticker
        # for the whole day (its held leg's TP included) even with ~30 min of
        # window left. Save the partial snapshot -- 17:00 uses the best one
        # written -- but exit non-zero so the tick does NOT write the marker
        # and every remaining in-window tick retries a cleaner pull, which
        # simply overwrites this file.
        print(f"PARTIAL: {len(market.skipped_chains)} chain(s) failed "
              f"({[tk for tk, _ in market.skipped_chains][:8]}); snapshot "
              f"saved anyway; exiting 1 so remaining window ticks retry.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
