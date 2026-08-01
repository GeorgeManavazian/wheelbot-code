"""Pure strike-selection and marking primitives over an OptionsChain frame.
No I/O, no engine state — consumed by the Wheel engine (sub-project 2)."""
from __future__ import annotations
import pandas as pd
from .chain import Contract, Mark

def derived_band(target_dte: int) -> tuple[int, int]:
    """DTE guard rail derived from the target. Floor rejects expiry stubs;
    ceiling rejects a monthly when the weekly is absent. Display-only upstream."""
    return max(5, target_dte - 2), target_dte + 3

def liquidity_ok(chain, date, contract, cfg):
    """A2 liquidity gate: is this contract liquid enough to OPEN a short in?
    Pure veto predicate -> (True, "") or (False, reason). Evaluated AFTER
    selection (a row-filter before selection silently moves the sold delta --
    measured 0.28 -> 0.40 on the GDX fixture -- instead of refusing the trade)
    and only on entry/roll-destination paths: never on closes, expiry, marks,
    held-only rows, or covered calls (owner-provisional 2026-08-01).

    A threshold left None disables that leg -- backtest chains carry no
    OI/volume columns, so backtest configs run rel-spread only (a declared
    one-sentence divergence, like the A18 print-vs-quote modes). With a
    threshold SET, a missing/NaN value FAILS: a contract whose liquidity
    cannot be measured is not one to sell.

    Rel-spread denominator is the computed midpoint (ask-bid)/((bid+ask)/2),
    never the `mid` column -- live's `mid` is Schwab's mark, and using the
    column would score the same contract differently in the two engines."""
    if (cfg.liq_max_rel_spread is None and cfg.liq_min_open_interest is None
            and cfg.liq_min_volume is None):
        return True, ""
    rows = chain[(chain["date"] == date) & (chain["expiry"] == contract.expiry)
                 & (chain["strike"] == contract.strike)
                 & (chain["right"] == contract.right)]
    # mirror select_contract: a spliced mark-only row must never answer for a
    # tradeable contract (duplicate-key order sensitivity, A2 skeptic F7)
    if "held_only" in rows.columns:
        rows = rows[~rows["held_only"].fillna(False).astype(bool)]
    if rows.empty:
        return False, "row_missing"
    row = rows.iloc[0]
    bid, ask = float(row["bid"]), float(row["ask"])
    den = (bid + ask) / 2.0
    if not (den > 0) or ask < bid:
        return False, "no_two_sided_market"
    if (cfg.liq_max_rel_spread is not None
            and (ask - bid) / den > cfg.liq_max_rel_spread):
        return False, "rel_spread"
    for field, thresh in (("open_interest", cfg.liq_min_open_interest),
                          ("volume", cfg.liq_min_volume)):
        if thresh is None:
            continue
        if field not in chain.columns:
            return False, field
        try:
            v = float(row[field])
        except (TypeError, ValueError):
            return False, field
        if v != v or v < thresh:          # NaN or below the floor
            return False, field
    return True, ""


def select_contract(chain, date, right, target_delta, target_dte, root, min_strike=None):
    """Expiry FIRST (nearest target_dte within derived_band, from expiries visible
    on `date` only), THEN strike (nearest |delta| within that one expiry).
    With `min_strike`, expiries are tried in nearest-DTE order (tie -> longer-
    dated) and the first one containing a strike >= min_strike is used; only
    when no in-band expiry qualifies -> None. Deterministic; None -> sit in cash."""
    lo, hi = derived_band(target_dte)
    cand = chain[(chain["date"] == date) & (chain["right"] == right)]
    # Mark-only rows are not tradeable. live/held_legs.py splices in the legs the
    # bounded chain cannot see so the take-profit can still act on them; those
    # rows were pulled by OCC symbol for a position already held, and are not
    # part of the chain the bot actually surveyed. Selecting one enters a
    # contract the bot never saw — and because run_daily builds ONE market for
    # all 25 accounts, the leg one account holds would otherwise appear in every
    # other account's candidate set. Column absent on every pre-2026-07-31 chain,
    # where absence correctly means "tradeable".
    if "held_only" in cand.columns:
        cand = cand[~cand["held_only"].fillna(False).astype(bool)]
    if cand.empty:
        return None
    dtes = cand.groupby("expiry")["dte"].first()
    dtes = dtes[(dtes >= lo) & (dtes <= hi)]
    if dtes.empty:
        return None
    err = (dtes - target_dte).abs()
    for exp in sorted(dtes.index, key=lambda e: (err[e], -dtes[e])):
        e = cand[cand["expiry"] == exp]
        if min_strike is not None:
            e = e[e["strike"] >= min_strike]
            if e.empty:
                continue
        row = e.loc[(e["delta"].abs() - abs(target_delta)).abs().idxmin()]
        return Contract(root, row["expiry"], float(row["strike"]), right)
    return None

def select_roll_contract(chain, date, right, strike, current_expiry, target_dte, root):
    """Roll destination (repair spec amendment 2026-07-13b): the canonical
    credit roll is SAME STRIKE, out in time. Candidates are expiries STRICTLY
    beyond the held leg's expiry that carry the held strike, nearest to
    (current_expiry + target_dte) — one config cycle further out; tie ->
    longer-dated. Deterministic; None -> no roll today."""
    cand = chain[(chain["date"] == date) & (chain["right"] == right)
                 & (chain["strike"] == strike)]
    if cand.empty:
        return None
    exps = cand.groupby("expiry")["dte"].first()
    # tenor guard: the extension (new expiry - held expiry) must sit within the
    # same derived band the entry uses — without it, a sparse strike grid could
    # silently roll a 7-DTE campaign months out (no such expiry -> no roll).
    lo, hi = derived_band(target_dte)
    cur = pd.Timestamp(current_expiry)
    ext = (exps.index - cur).days
    exps = exps[(ext >= max(1, lo)) & (ext <= hi)]
    if exps.empty:
        return None
    anchor = cur + pd.Timedelta(days=target_dte)
    best = sorted(exps.index, key=lambda e: (abs((e - anchor).days), -exps[e]))[0]
    return Contract(root, best, float(strike), right)

def option_mark(chain, date, contract):
    # INVARIANT: `chain` is single-root. Both callers guarantee it — BatchMarket
    # groups by ticker, LiveMarket pulls one ticker's chain_frame at a time — so
    # matching on (date,expiry,strike,right) can't cross to another underlying's
    # like-struck option. If a multi-root chain is ever passed here, add a root
    # filter (needs a `root` column on the chain frame). See audit 2026-07-17 (I9).
    m = ((chain["date"] == date) & (chain["expiry"] == contract.expiry)
         & (chain["strike"] == contract.strike) & (chain["right"] == contract.right))
    r = chain[m]
    if r.empty:
        return None
    row = r.iloc[0]
    return Mark(float(row["bid"]), float(row["ask"]), float(row["mid"]))

def intrinsic_value(right, strike, underlying):
    return max(strike - underlying, 0.0) if right == "P" else max(underlying - strike, 0.0)

def expiry_underlying(chain, contract):
    r = chain[chain["date"] == contract.expiry]
    if r.empty:
        return None
    return float(r.iloc[0]["underlying"])
