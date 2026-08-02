"""A8 (rescoped 2026-08-02): broker-vs-state reconciliation for real-money mode.

Early assignment is NOT modelled anywhere -- paper mode simulates its own
fills, so it structurally cannot happen there. What CAN happen, the day real
money is ever used, is the broker's book diverging from the bot's notebook:
an American put assigned early, shares called away before an ex-div, a manual
trade, an OCC restatement. The defense is detection, never inference:
`diff_positions` compares the two books and returns every mismatch classified
per the A8 taxonomy. FREEZE divergences take the A10 lifecycle (sticky
`recon_frozen` on the position, daily alert, MANUAL clearing only -- a human
applies the restatement to the notebook by hand). Nothing is ever auto-applied:
a mis-attributed corporate action auto-applied as an assignment is the $42,750
class from the audit.

No engine wiring exists yet, deliberately. `real_money: true` in config.json
makes both runners refuse to start until the reconciler is wired (and order
code exists). This module is pure and offline-testable: no client import.

broker_view shape (a thin fetch/normalize mapper lands with real-money mode):
    {"cash": float,
     "options": {(root, "YYYY-MM-DD", strike, right): signed_contracts},
     "equity": {ticker: signed_shares}}
Shorts are negative in `options`. Missing key == flat.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# T4/T5 boundary (owner D2, provisional): below this, a cash-only drift is the
# unmodelled-fee class (A13: exchange/OCC/regulatory, assignment $5-25) --
# ALERT. At or above it, the drift is no longer explicable as fees -- FREEZE.
DEFAULT_CASH_EPSILON = 25.0

# Cash equal "to the cent": half a cent absorbs float noise, nothing real.
_CENT = 0.005


@dataclass(frozen=True)
class Divergence:
    kind: str        # taxonomy tag, see diff_positions
    subject: str     # ticker, or "ROOT YYYY-MM-DD <strike><right>" for a leg
    expected: object  # what the state says
    observed: object  # what the broker says
    severity: str    # "FREEZE" | "ALERT"


def _leg_key(contract) -> tuple:
    return (contract.root,
            pd.Timestamp(contract.expiry).date().isoformat(),
            float(contract.strike), contract.right)


def _leg_label(key: tuple) -> str:
    root, expiry, strike, right = key
    return f"{root} {expiry} {strike:g}{right}"


def diff_positions(broker_view: dict, state,
                   cash_epsilon: float = DEFAULT_CASH_EPSILON) -> list:
    """Every mismatch between the broker's book and the bot's state, classified:

    early_put_assignment          T1  short put gone, +100*n shares      FREEZE
    early_put_assignment_partial  T2  k of n assigned, +100*k shares     FREEZE
    early_call_assignment         T3  short call gone, -100*n shares     FREEZE
    cash_drift                    T4  positions match, |delta| < eps     ALERT
    cash_drift                    T5  positions match, |delta| >= eps    FREEZE
    unknown_position              T6  broker holds what state never did  FREEZE
    leg_vanished                  T7  leg gone, no share/cash trace      FREEZE
    option_count_mismatch / equity_mismatch    unclassifiable fallback   FREEZE

    Cash is judged ONLY when every position matches (T4/T5 are defined on a
    matching book): while positions diverge, cash necessarily diverges too,
    and the restatement is the human's job. Deterministic order: option
    divergences (state order), unknown broker legs, equity, cash.

    Precondition (skeptic 2026-08-02): at most ONE short leg per root, which
    is how the wheel holds positions (one position per ticker per account).
    Two vanished shorts on the same root can each claim the same share delta
    (both classified as assignments instead of one assignment + one
    leg_vanished). Severity-safe -- every such shape still FREEZEs -- but the
    classification is only trustworthy under the precondition.
    """
    # The mapper's contract is load-bearing: a malformed broker_view must die
    # with a message, not a bare KeyError three frames deep (skeptic F2/F3).
    if "cash" not in broker_view:
        raise ValueError("broker_view missing 'cash' -- not a valid mapper output")
    for key in broker_view.get("options", {}):
        if not isinstance(key[1], str):
            raise ValueError(f"broker_view option key {key!r}: expiry must be "
                             f"a 'YYYY-MM-DD' string, got {type(key[1]).__name__}")
    out = []
    b_opts = dict(broker_view.get("options", {}))
    b_eq = dict(broker_view.get("equity", {}))

    exp_eq = {}
    for pos in state.positions:
        exp_eq[pos["ticker"]] = exp_eq.get(pos["ticker"], 0) + pos["shares"]

    explained_eq = set()   # tickers whose share delta an option row consumed
    for pos in state.positions:
        sh = pos["short"]
        if sh is None:
            continue
        key = _leg_key(sh["contract"])
        root, _expiry, _strike, right = key
        expected = -sh["contracts"]
        observed = b_opts.pop(key, 0)
        if observed == expected:
            continue
        sh_delta = b_eq.get(root, 0) - exp_eq.get(root, 0)
        label = _leg_label(key)
        if right == "P" and observed == 0 and sh_delta == 100 * sh["contracts"]:
            kind, subject = "early_put_assignment", root
        elif (right == "P" and expected < observed < 0
              and sh_delta == 100 * (observed - expected)):
            kind, subject = "early_put_assignment_partial", root
        elif right == "C" and observed == 0 and sh_delta == -100 * sh["contracts"]:
            kind, subject = "early_call_assignment", root
        elif observed == 0 and sh_delta == 0:
            kind, subject = "leg_vanished", label
        else:
            kind, subject = "option_count_mismatch", label
        if subject == root:      # an assignment shape consumed the share delta
            explained_eq.add(root)
        out.append(Divergence(kind, subject, expected, observed, "FREEZE"))

    for key in sorted(b_opts):
        out.append(Divergence("unknown_position", _leg_label(key),
                              0, b_opts[key], "FREEZE"))

    for tkr in sorted(set(exp_eq) | set(b_eq)):
        if tkr in explained_eq:
            continue
        exp_n, obs_n = exp_eq.get(tkr, 0), b_eq.get(tkr, 0)
        if exp_n == obs_n:
            continue
        kind = "equity_mismatch" if tkr in exp_eq else "unknown_position"
        out.append(Divergence(kind, tkr, exp_n, obs_n, "FREEZE"))

    if not out:
        delta = abs(broker_view["cash"] - state.cash)
        if delta > _CENT:
            sev = "ALERT" if delta < cash_epsilon else "FREEZE"
            out.append(Divergence("cash_drift", "cash",
                                  state.cash, broker_view["cash"], sev))
    return out
