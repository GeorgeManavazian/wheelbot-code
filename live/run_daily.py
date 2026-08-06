"""Daily paper-step runner. Data-only, simulated fills, NO order code.

  PYTHONPATH=. .venv-live/bin/python live/run_daily.py --n 5 --capital 100000
  PYTHONPATH=. .venv-live/bin/python live/run_daily.py --smoke   # 3-ticker live test
"""
from __future__ import annotations

from live.paths import in_state
import argparse
import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

import pandas as pd

from src.engine_v2.options.portfolio import PortfolioState, step_one_day
from src.engine_v2.options.wheel import WheelConfig
from live.state import load_state, save_state
from live.market_live import LiveMarket
from live.universe import UNIVERSE
from live.accounts import all_accounts, account_paths, account_label
from live.alerts import send_alert
from live.gaps import append_gap, append_correction
from live.held_legs import merge_held_legs, merge_pulled, held_contracts
from live.escalations import update_escalations, ESCALATION_DAYS
from live.config import load_run_config

ET = ZoneInfo("America/New_York")

FROZEN = dict(put_delta=0.30, call_delta=0.50, target_dte=11,
              take_profit_pct=0.60, call_min_strike="basis",
              # DOWN-ONLY tactical gate (A/B'd 2026-07-18): reject only a FALLING
              # short-horizon leg (9d/20d < -1%). A put seller loses on a fall, not
              # a rise, so keep up-legs (winners). The symmetric + structural gates
              # backtested worse (symmetric -71% P&L, structural went negative) and
              # were dropped. Guards the AA/VALE falling-knife names.
              chop_max_fast_fall=0.01,
              # A2 liquidity gate (2026-08-01): ON in production, entries only.
              # Audit: an ungated bot requested 757 DOW contracts vs ~84/day
              # traded; this gate would have refused 114 of 145 real entries
              # (fantasy fills, not lost profit). Denominator = midpoint,
              # computed inline — a provisional owner decision, declared in
              # the A2 evidence block. Missing/NaN field with a set threshold
              # = refuse.
              liq_max_rel_spread=0.10, liq_min_open_interest=250,
              liq_min_volume=25,
              # Collateral-yield floor (spec 2026-08-04, engine commit 682823e).
              # VETO on new short-put entries whose credit is a negligible return
              # on the cash the strike locks up: (credit/strike)*(365/dte) < this.
              # The pre-existing minimum credit is ABSOLUTE ($2.50/contract here),
              # so a $1,000-strike put locking $100,000 cleared it on $2.50 -- the
              # owner's $10-against-$100k case. Measured medians 2024-01 -> 2026-07
              # are 18.3/30.9/45.6% annualised at 0.20/0.30/0.40 delta and the
              # least generous real name (XLU) is 8.4%, so 0.08 is a junk filter,
              # not a tuning surface. Wired into FROZEN 2026-08-06: the engine had
              # it from 682823e but this dict did not set it, so the live bot was
              # running WITHOUT it while _STATUS reported the config work done.
              min_ann_yield_on_collateral=0.08,
              # Intrinsic filter (same spec/commit, same 2026-08-06 wiring gap).
              # A short put whose credit is a large fraction of the strike is not
              # a volatility sale -- it is mostly INTRINSIC value, a stock purchase
              # wearing a premium costume. On the 2.53y 500k run, 2 of 299 entries
              # exceeded 3% (NOK 37.2%, HL 9.4%) and carried -$47,150 of the
              # -$64,650 total share-leg loss: 73% of the damage from 0.7% of the
              # trades. Median entry is 0.52% of strike, so 3% is nearly inert.
              max_credit_pct_of_strike=0.03,
              # NOT SET: earnings_blackout. The flag alone would be a ZOMBIE GATE
              # --  _live_market() never constructs LiveMarket with `earnings=`,
              # so earnings_dates() returns None, in_blackout() allows, and the
              # gate reads as ON while doing nothing. It also needs data/earnings/
              # *.parquet on the VPS, which is untracked and outside the
              # live/ src/ deploy/ rsync. Owner and Claude to wire together.
              # A13 (2026-08-02): pass-through fees (OCC/ORF/SEC/TAF) as one
              # flat per-contract-side adder -- PROVISIONAL $0.05, owner
              # verifies against the first real statement (the audit's
              # implied $0.30 double-counts exchange fees Schwab embeds).
              # Assignment fee $0 at Schwab since 2019 (VERIFY) -- wired so a
              # nonzero is a one-line change. Dataclass defaults stay 0.0:
              # goldens and every historical backtest stay byte-identical.
              fees_per_contract=0.05, fee_per_assignment=0.0)


def zombie_check(skipped_closes: int, universe_size: int,
                 chain_attempts: int, chains_ok: int, threshold: float) -> bool:
    """True when this run pulled so little data that it is a FAILED run, not a
    quiet one. A zombie run must leave no marker, so the next tick retries it.

    2026-07-24 is why this exists: all 547 tickers failed, the run exited 0,
    booked no trades, and its wrapper wrote the 'done' marker anyway -- a dead
    day permanently recorded as complete, and unrecoverable.

    The two feeds are judged SEPARATELY because they cover different-sized
    populations. Price history is pulled for the whole universe; option chains
    only for held + good-to-rent, measured live at ~5% of it. A single pooled
    ratio (the pre-2026-07-29 version) therefore capped chain failures at ~7%
    and could NEVER trip a 50% threshold -- so a total outage of the option-chain
    endpoint, which live/data.py notes is the flaky one ('the full chain 502s'),
    produced exactly the 2026-07-24 outcome through a door the guard could not
    see. Pooling was also wrong in the other direction: 260 closes failures plus
    15 chain failures summed to 50.3% and would discard a day on which 287
    tickers had perfectly good data."""
    if universe_size <= 0:
        return True
    if (skipped_closes / universe_size) >= threshold:
        return True
    # No chain candidates at all is a legitimately quiet day: nothing held and
    # nothing passed the chop gate, so there was nothing to pull.
    if chain_attempts <= 0:
        return False
    # But if we DID try and got nothing usable, no account can enter, take
    # profit, or write a covered call. That is a failed day, not a quiet one.
    if chains_ok == 0:
        return True
    return ((chain_attempts - chains_ok) / chain_attempts) >= threshold


def is_trading_day(market, obs, min_fraction: float = 0.5) -> bool:
    """True when `obs` actually looks like a session, judged from the data itself.

    There is no market-holiday calendar anywhere in this bot, so on Thanksgiving
    or Good Friday the weekday gate opens, price history still returns (with the
    PRIOR session's close as its last row), and the bot books a full paper day at
    stale quotes -- fabricating a trading day that never happened and polluting
    the equity series with a phantom row.

    Rather than vendor a holiday calendar (which silently rots), ask the data:
    on a real session the exchange publishes a bar dated `obs` for essentially
    every liquid name. On a holiday it publishes none. A weekend is already
    excluded upstream by the tick's DOW gate."""
    day = pd.Timestamp(obs).normalize()
    closes = getattr(market, "_closes", {}) or {}
    if not closes:
        return False
    have = sum(1 for s in closes.values()
               if s is not None and len(s) and day in s.index)
    return (have / len(closes)) >= min_fraction


def already_stepped(snapshot_path, obs) -> bool:
    """True when this account already has a snapshot for `obs`. Guards the
    5-minute retry window against double-stepping a day (which appends a second
    snapshot row and re-books the day's trades)."""
    from live.snapshots import load_snapshots
    target = pd.Timestamp(obs).normalize()
    try:
        for snap in load_snapshots(snapshot_path):
            if pd.Timestamp(snap["date"]).normalize() == target:
                return True
    except (ValueError, KeyError, OSError):
        return False       # unreadable history -> let the step proceed
    return False


def _read_trades_tickers(path) -> set:
    """C6: tickers this account ever traded, from its ledger. Missing or
    partially unreadable file -> whatever parsed (benchmark coverage, not
    money path)."""
    out = set()
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("ticker"):
                    out.add(r["ticker"])
    except OSError:
        pass
    return out


def write_benchmark_row(accounts, market, day, *, smoke,
                        paths_fn=None, bench_path=None) -> bool:
    """C6: one benchmark row per stepped day -- SPY + every name any account
    ever traded, closes from the SAME pull the decisions used. price_history
    is retroactive, so the store is complete-backwards from its first write;
    no backfill logic exists or is needed. Smoke never writes (A23 class).
    Any failure is a printed disclosure, never a raise -- yardstick coverage,
    not money path. `paths_fn`/`bench_path` exist so tests never touch the
    real store (WHEELBOT_STATE_DIR is read at import time)."""
    if smoke:
        return False
    try:
        pf = paths_fn or (lambda cap, n: _paths(cap, n, smoke=False))
        traded = {"SPY"}
        for (cap_, n_) in accounts:
            traded |= _read_trades_tickers(pf(cap_, n_)["trades"])
        closes = getattr(market, "_closes", {})
        bench = {t: float(closes[t].iloc[-1]) for t in sorted(traded)
                 if t in closes and len(closes[t])}
        if not bench:
            return False
        bpath = bench_path or in_state("benchmark.jsonl")
        os.makedirs(os.path.dirname(bpath) or ".", exist_ok=True)
        with open(bpath, "a") as f:
            f.write(json.dumps({"date": day, "closes": bench}) + "\n")
        return True
    except Exception as e:                     # noqa: BLE001 -- disclosure
        print(f"C6: benchmark row not written ({type(e).__name__}: {e})"
              f" -- yardstick has a hole today, trading unaffected")
        return False


def _trade_row(t):
    c = t.contract
    return {"date": pd.Timestamp(t.date).isoformat(), "action": t.action,
            "ticker": c.root, "strike": c.strike, "right": c.right,
            "expiry": pd.Timestamp(c.expiry).isoformat(), "contracts": t.contracts,
            "price": t.price_per_contract, "cash_after": t.cash_after,
            "campaign": t.campaign_id}


def held_marks_failed(merged: dict) -> bool:
    """B4: the zombie gate judges closes and chains but not held-leg marks --
    the third population, and the one carrying 100%% of realized P&L. True
    when held legs were requested and NONE could be marked (endpoint error or
    every leg unquoted): every take-profit is suspended, which is a FAILED
    run (retry), not a quiet one."""
    if not merged.get("requested"):
        return False
    if merged.get("error"):
        return True
    # group-skeptic F2: a 0.00x0.00 book on every held leg is ANSWERED but
    # unquotable -- the endpoint is alive and retrying cannot change the
    # book; wedging the whole night (78 retry alerts, zero accounts stepped)
    # over a worthless-but-real book was the snapshot-retry bug through the
    # quote door. Wholesale failure = the endpoint answered for NOTHING.
    return merged.get("answered", 0) == 0


def collect_unsettled(warnings) -> dict:
    """A14: {leg label -> days past expiry} for every unsettleable-expiry
    warning. The 'bound' on the refusal is escalation -- these feed one loud
    daily alert, so a leg stuck because its close never arrives (delisting,
    corporate action) can no longer wait in silence."""
    out = {}
    for w in warnings:
        if w[1] != "expiry_unsettleable":
            continue
        c = w[2]
        late = (pd.Timestamp(w[0]).normalize()
                - pd.Timestamp(c.expiry).normalize()).days
        key = f"{c.root} {c.strike}{c.right} {pd.Timestamp(c.expiry).date()}"
        out[key] = max(out.get(key, 0), late)
    return out


def paper_step(state, market, cfg, n_slots, trades_path, state_path,
               snapshot_path=None):
    day = market._obs
    result = step_one_day(state, market, day, cfg, selector="chop", n_slots=n_slots)
    # A2: a fully-gated day must not print like a quiet one. paper_step used
    # to discard result.warnings entirely, which would have made the liquidity
    # veto invisible in production -- the same silent-failure class as the
    # 2026-07-29 audit findings.
    gated = sorted({w[2] for w in result.warnings
                    if w[1] in ("entry_gated_illiquid", "entry_gated_unclosable")})
    if gated:
        print(f"liquidity gate: entries refused on {len(gated)} ticker(s) "
              f"{gated[:8]} (A2/A3 veto -- illiquid or unclosable, not quiet)")
    naked = sorted({w[2] for w in result.warnings
                    if w[1] == "covered_call_unreachable"})
    if naked:
        print(f"covered call unreachable on {len(naked)} ticker(s) {naked[:8]} "
              f"-- shares sit uncovered today (A4)")
    # A14: EVERY other warning kind reaches the log too -- expiry_unsettleable
    # above all (a leg past expiry whose close is missing sits open until a
    # later run can settle it; that must never be silent). Generic by reason
    # so a future warning kind can't slip back into the void.
    _handled = {"entry_gated_illiquid", "entry_gated_unclosable",
                "covered_call_unreachable"}
    other = {}
    for w in result.warnings:
        if w[1] not in _handled:
            other.setdefault(w[1], []).append(w[2])
    def _label(s):
        # skeptic F1: format Contracts as "TMO 512.5P 2026-07-10", everything
        # else via str -- and never sort raw mixed types (None vs str raises)
        if hasattr(s, "root") and hasattr(s, "strike"):
            return f"{s.root} {s.strike}{s.right} {pd.Timestamp(s.expiry).date()}"
        return str(s)
    for reason, subjects in sorted(other.items()):
        subj = sorted({_label(s) for s in subjects})
        print(f"warning {reason}: {len(subjects)} occurrence(s) {subj[:8]}")
    # State FIRST (the source of truth). If it saved, the log/snapshot appends
    # that follow are secondary — a failure there leaves state correct + an
    # incomplete log (recoverable), never a log claiming trades the reloaded
    # state doesn't reflect (which could re-trade a position).
    save_state(state, state_path)
    os.makedirs(os.path.dirname(trades_path) or ".", exist_ok=True)
    with open(trades_path, "a") as f:
        for t in result.trades:
            f.write(json.dumps(_trade_row(t)) + "\n")
    if snapshot_path is not None:
        from live.snapshots import snapshot, append_snapshot
        append_snapshot(snapshot_path, snapshot(state, result.equity, day))
    return result


def _live_market(universe, held, obs, client, target_dte, chains,
                 chop_max_ma_spread=None, chop_max_fast_spread=None,
                 chop_max_fast_fall=None):
    """`chains` is REQUIRED and names the chain source out loud (A16):
    a dict = the day's RTH snapshot (17:00 decision runs pass this; a candidate
    absent from it is a failed pull, counted in skipped_chains); None = pull
    live from Schwab, which after 16:00 ET is the post-close ghost book -- only
    the RTH snapshot runner and --smoke may pass it."""
    from live.data import daily_closes, chain_frame
    if chains is None:
        # pass obs into chain_frame so the chain's `date` column == the run's obs
        # day (else a midnight-crossing run stamps chains with a different date
        # than the engine filters on, silently reading every chain as empty).
        chain_fn = lambda tk: chain_frame(client, tk, target_dte, obs_date=obs)
    else:
        def chain_fn(tk):
            if tk not in chains:
                raise KeyError(f"{tk} not in the RTH chain snapshot")
            return chains[tk]
    return LiveMarket(universe, held, obs,
                      closes_fn=lambda tk: daily_closes(client, tk),
                      chain_fn=chain_fn,
                      chop_max_ma_spread=chop_max_ma_spread,
                      chop_max_fast_spread=chop_max_fast_spread,
                      chop_max_fast_fall=chop_max_fast_fall)


def missing_held_rows_day(position_lists, obs, alert_fn) -> dict:
    """A21-D2 (owner 2026-08-02): the RTH snapshot exists but carries no held
    rows -- the held-leg pull failed every in-window tick, or the file is
    pre-A21. The day STEPS anyway: legs inside the chain window mark off the
    RTH chains as usual; legs outside keep their carried mark (mark-None
    mechanically suspends their TP -- trading off a missing photo is exactly
    what we refuse to do), and the owner gets ONE alert naming the count.
    Zero held legs -> the missing key is perfectly healthy, total silence.

    Returns the merge-stats shape. The error string routes through the
    existing degraded-day print; the caller must NOT treat this as
    held_marks_failed (no 17:00 retry can rebuild an RTH snapshot)."""
    contracts = held_contracts(position_lists)
    stats = {"requested": len(contracts), "merged": 0, "unquoted": [],
             "no_chain": [], "error": None, "answered": 0}
    if not contracts:
        return stats
    stats["error"] = (f"held rows absent from the RTH snapshot -- A21-D2: "
                      f"{len(contracts)} held leg(s) step with carried marks, "
                      f"EOD TP suspended today")
    alert_fn(f"HELD-LEG RTH QUOTES MISSING {pd.Timestamp(obs).date()}",
             f"The RTH snapshot has no held-leg quotes (pull failed all "
             f"window, or pre-A21 file). {len(contracts)} held leg(s) "
             f"outside the chain window carry yesterday's marks and their "
             f"EOD take-profit is suspended for today (owner D2, "
             f"2026-08-02). Legs inside the chain window are unaffected. "
             f"The day steps normally otherwise.")
    return stats


def resolve_held_merge(market, client, position_lists, obs, smoke, snap,
                       alert_fn) -> tuple:
    """A21 (owner D1/D2, 2026-08-02): outside --smoke the held-leg quotes
    come from the RTH snapshot store, NEVER a 17:00 get_quotes -- at that
    hour the options market has been closed for an hour and the live book is
    the 3-4x-wide ghost that refused the RIG buy-back the real market
    offered all day. Returns (merged_stats, held_d2).

    Branches: --smoke keeps the live pull as a connectivity probe
    (throwaway, disclosed post-close) · no snapshot at all -> inert zeros
    (the day never steps; it is classified holiday/gap/retry downstream) ·
    snapshot without held rows -> the D2 carried-marks day · snapshot with
    held rows -> merge them, no network."""
    if smoke:
        return merge_held_legs(market, client, position_lists, obs), False
    if snap is None:
        return {"requested": 0, "merged": 0, "unquoted": [], "no_chain": [],
                "error": None, "answered": 0}, False
    from live.chain_store import load_held_rows
    held = load_held_rows(obs)
    if held is None:
        return missing_held_rows_day(position_lists, obs, alert_fn), True
    merged = merge_pulled(market, held, obs)
    _print_held_drift(position_lists, held)
    return merged, False


def _print_held_drift(position_lists, held) -> list:
    """A21 skeptic F7: a leg held at 17:00 but absent from the stored 15:2x
    pull (position set changed, or its account's state file was unreadable at
    snapshot time -- the runner's `if st is None: continue` door) appeared in
    NO stat: not merged, not unquoted, not no_chain. Its TP was suspended
    with zero disclosure. Cross-check and say it out loud."""
    try:
        current = held_contracts(position_lists)
    except Exception:
        return []   # F1 class: malformed positions already degrade loudly
    row_keys = {(tk, r["expiry"], r["strike"], r["right"])
                for tk, rows in held["rows_by_ticker"].items() for r in rows}
    unq = set(held["unquoted"])
    missing = [c["symbol"] for c in current
               if (c["ticker"], c["expiry"], c["strike"], c["right"])
               not in row_keys and c["symbol"] not in unq]
    if missing:
        print(f"HELD-LEG DRIFT -- {len(missing)} leg(s) held NOW but absent "
              f"from the stored RTH pull: {missing}. Their marks carry and "
              f"their EOD TP is suspended today (position set changed since "
              f"15:2x, or a state file was unreadable at snapshot time).")
    return missing


def holiday_note(obs, smoke: bool = False) -> None:
    """D2 (owner 2026-08-01): a weekday the bot classifies as a holiday gets
    ONE informational email. On a real NYSE holiday (~9/yr) it is benign; on a
    trading day it is the only signal that the feed served stale bars and the
    day is being lost (the 2026-07-24 class) -- is_trading_day cannot tell
    those apart from inside. Delivery failure is tolerated (exit stays 0, the
    day is genuinely not a gap on a real holiday); the alerts spool retries.
    Quiet on --smoke (a connectivity check is not a holiday signal) and on
    weekend obs dates (manual runs; group skeptic F5)."""
    if smoke or pd.Timestamp(obs).weekday() >= 5:
        return
    day = str(pd.Timestamp(obs).date())
    send_alert(
        f"no session {day} -- holiday?",
        f"The bot classified {day} as a market holiday (no bar dated today "
        f"across the universe) and stepped nothing.\n\n"
        f"Expected only on real NYSE holidays (~9/yr). If the market WAS "
        f"open today, the price feed served stale bars and this day is being "
        f"silently lost -- check the feed and the VPS clock.",
    )


def snapshot_missing_outcome(market, universe, obs, threshold) -> str:
    """A16: classify a decision run whose RTH chain snapshot is absent.

    'retry'   -- the closes feed itself is dead. Judged FIRST because with an
                 empty market is_trading_day() reads a token outage as a
                 holiday; the caller falls through to the zombie path (exit 1)
                 so the tick retries -- closes ARE still pullable later.
    'holiday' -- no session today. There was never a snapshot to miss; the
                 caller exits 0 quietly and records no gap.
    'gap'     -- a real trading day with no snapshot. Owner decision
                 2026-07-31: SKIP the day (gap + alert + marker), never fall
                 back to a live post-close pull -- no later tick can recreate
                 a 15:2x-15:5x snapshot, so retrying cannot help."""
    if zombie_check(len(market.skipped_closes), len(universe), 0, 0, threshold):
        return "retry"
    if not is_trading_day(market, obs):
        return "holiday"
    return "gap"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",  # 1 account, 3 tickers, throwaway store
                    help="quick live connectivity check (100k/N5 on GDX/SLV/XOP -> _smoke store)")
    ap.add_argument("--force", action="store_true",
                    help="bypass the A11 clock gate (manual/backfill use only)")
    args = ap.parse_args()

    # A11: the decision run must not step before the 16:00 ET close --
    # settlement reads the expiry day's OWN close and equity marks are
    # close-to-close, so an early run books a full paper day off stale or
    # partial prices (2026-07-24: a 04:13 ET catch-up booked every entry from
    # the prior day's quotes). Nonzero exit: no done-marker, and the tick's
    # 17:00-23:30 window retries as designed. The RTH SNAPSHOT phase has the
    # inverse gate (refuse OUTSIDE 09:30-16:00) -- the two never conflict.
    # --smoke is exempt (throwaway store, connectivity only).
    now_et = dt.datetime.now(ET)
    if not args.force and not args.smoke and now_et.hour < 17:
        # < 17, not < 16 (skeptic F1): Schwab's daily bar is not reliably
        # SETTLED until ~17:00 (the old loop's "data settled" constant), and a
        # manual 16:0x run that steps on a preliminary close gets locked in by
        # already_stepped -- the official 17:00 rerun could never correct it.
        print(f"A11: refusing to step at {now_et:%H:%M} ET -- settlement and "
              f"marks need the SETTLED close (~17:00). No marker written; the "
              f"tick retries inside its 17:00-23:30 window. --force overrides.")
        return 2

    # A23: --smoke must be throwaway END TO END. State/trades/snapshots
    # already isolate to the _smoke store, but the zombie/partial/failure
    # paths called the REAL send_alert and appended to the REAL gaps.jsonl --
    # a dead feed during a casual connectivity check emailed the owner and
    # permanently gapped a day the real 17:00 run may go on to complete.
    # Exit codes are unchanged (a failed smoke run still exits nonzero).
    if args.smoke:
        def _alert(subject, body):
            print(f"[smoke] alert suppressed: {subject}")
            return True
        def _gap(date, reason, **kw):
            print(f"[smoke] gap suppressed: {date} {reason}")
            return True
        _correction = _gap
    else:
        _alert, _gap, _correction = send_alert, append_gap, append_correction

    # A8: real-money mode requires a wired position reconciler
    # (live/reconcile.py) and order code; neither exists. Refuse BEFORE any
    # client build or pull -- a refused run must not burn API quota or
    # half-execute. Loaded here (not at the old post-pull site) for the same
    # reason; a malformed config now also aborts before any network work.
    run_cfg = load_run_config()
    if run_cfg["real_money"]:
        msg = ("real_money=true in config.json, but real-money mode requires "
               "a wired position reconciler (A8) and order code; neither "
               "exists. Refusing to run. Set real_money back to false.")
        print(f"A8: {msg}")
        _alert("WHEELBOT: real_money refused", msg)
        return 3

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "schwab"))
    from schwab_client import get_client
    client = get_client()

    universe = ["GDX", "SLV", "XOP"] if args.smoke else UNIVERSE
    # The trading date MUST come from Eastern, not the box clock. The VPS runs
    # UTC and wheelbot_tick.sh gates in ET, so in winter (EST, UTC-5) any retry
    # between 19:00 and 24:00 ET is already "tomorrow" in UTC -- the run would
    # stamp the wrong trading date and settle expiries against the wrong close.
    obs = pd.Timestamp(dt.datetime.now(ET).date())
    # strategy params are shared across accounts; capital/N vary per account
    cfg = WheelConfig(ticker="SPY", starting_capital=100_000.0, **FROZEN)
    accounts = [(100_000, 5)] if args.smoke else all_accounts()

    # Load every account's state FIRST so one shared market pull covers the union
    # of all held tickers (chains are pulled once for held-all + good-to-rent).
    loaded = {}
    held_all = set()
    for (cap, n) in accounts:
        paths = _paths(cap, n, smoke=args.smoke)
        state = load_state(paths["state"]) or PortfolioState(cash=float(cap), positions=[])
        loaded[(cap, n)] = (state, paths)
        held_all |= {p["ticker"] for p in state.positions}

    # A16: the decision run consumes the RTH snapshot pulled at 15:2x-15:5x ET.
    # Only --smoke (an explicit connectivity check on a throwaway store) may
    # still pull chains live. `snap is None` is handled after the pulls, where
    # holiday / outage / genuine gap can be told apart; passing {} meanwhile
    # keeps every candidate an honest skipped_chains entry instead of a pull.
    from live.chain_store import load_chain_snapshot, snapshot_pulled_at
    snap = None if args.smoke else load_chain_snapshot(obs)
    if snap is not None:
        # C1: pulled_at was written from day one and never read. One line:
        # how old is the book the 17:00 decisions are about to run on.
        pa = snapshot_pulled_at(obs)
        if pa:
            try:
                age_min = (dt.datetime.now(ET)
                           - dt.datetime.fromisoformat(pa)).total_seconds() / 60
                print(f"chain snapshot pulled {pa} ({age_min:.0f} min ago)")
            except ValueError:
                print(f"chain snapshot pulled {pa}")
    if args.smoke:
        # E4: the probe leg below needs GDX's chain, and a chop-gated day
        # would otherwise pull no chains at all -- held tickers always get
        # one (B5), so ride that path. Throwaway store; no state touched.
        held_all = held_all | {"GDX"}
    market = _live_market(universe, held_all, obs, client, cfg.target_dte,
                          None if args.smoke else (snap if snap is not None else {}),
                          cfg.chop_max_ma_spread, cfg.chop_max_fast_spread,
                          cfg.chop_max_fast_fall)
    # Mark the legs the bounded chain cannot see. MUST run before any account
    # steps: without it a position whose strike drifted outside the 12-strike
    # window is invisible to the take-profit and freezes at its last mark
    # (TMO 512.5P, carried at $11.30 while offered near $0.42). See held_legs.py.
    position_lists = [st.positions for (st, _p) in loaded.values()]
    if args.smoke:
        # E4: the smoke store holds no positions, so a connectivity check
        # used to pass with the whole held-leg quote path broken. Inject one
        # synthetic probe leg -- just below the pulled GDX chain's bottom
        # strike, so it is a real listed contract the chain does NOT carry --
        # into the MERGE CALL ONLY (never account state): the quote endpoint,
        # the splice, and the unquoted disclosure all get exercised. A probe
        # that fails to quote shows up as `unquoted`, which is itself signal.
        probe = _smoke_probe_leg(market, obs)
        if probe is not None:
            print(f"[smoke] probe leg: GDX "
                  f"{probe['short']['contract']['strike']}P "
                  f"{probe['short']['contract']['expiry']} (merge-only)")
            position_lists = position_lists + [[probe]]
    merged, held_d2 = resolve_held_merge(market, client, position_lists, obs,
                                         args.smoke, snap, _alert)
    if merged["requested"]:
        print(f"held-leg quotes: {merged['merged']} spliced into chains "
              f"({merged['requested']} distinct legs held"
              f"{', ' + str(len(merged['unquoted'])) + ' unquoted' if merged['unquoted'] else ''})")
    if merged["no_chain"]:
        # An unmarked held leg has its take-profit suspended for the day. This is
        # the same failure the merge exists to prevent, so it must never be
        # silent — merged==0 alone is indistinguishable from a healthy run.
        print(f"HELD LEG UNMARKED -- no chain for {len(merged['no_chain'])} leg(s): "
              f"{merged['no_chain']}. Their take-profit cannot fire today and "
              f"their equity mark is carried from the last successful mark.")
    if merged["error"]:
        # Not fatal: this is the pre-2026-07-31 behaviour, i.e. legs outside the
        # window stay unmarked. But it silently suspends the take-profit on them,
        # so it must be said out loud rather than swallowed.
        print(f"HELD-LEG QUOTE PULL FAILED -- {merged['error']}. Any position "
              f"outside the strike window will not be marked or take-profited today.")
    if market.skipped:
        print(f"skipped {len(market.skipped)} tickers (pull failures): "
              f"{[s[0] for s in market.skipped][:8]}")

    # group-skeptic F3: B5 widened the closes population to universe UNION
    # held -- ratios must divide by what was actually PULLED or a handful of
    # dead held-outside tickers inflates the ratio (and can exceed 100%).
    pulled_n = len(market.skipped_closes) + len(getattr(market, "_closes", {}))

    if not args.smoke and snap is None:
        outcome = snapshot_missing_outcome(market, range(pulled_n), obs,
                                           run_cfg["zombie_threshold"])
        if outcome == "holiday":
            print(f"{obs.date()}: not a trading session and no RTH chain "
                  f"snapshot — holiday. No paper day was stepped; this is "
                  f"not a gap.")
            holiday_note(obs, smoke=args.smoke)
            return 0
        if outcome == "gap":
            day = str(obs.date())
            msg = (f"{day}: no RTH chain snapshot for today -- the 15:2x-15:5x "
                   f"ET snapshot pass never succeeded (VPS down or token lapsed "
                   f"during the window). By owner decision (2026-07-31) the day "
                   f"is SKIPPED: entries, covered calls and the EOD take-profit "
                   f"cannot be priced off the post-close book. Expiries settle "
                   f"on the next run via the late-expiry path. Marker written; "
                   f"day recorded as a gap.")
            print(f"NO CHAIN SNAPSHOT -- {msg}")
            _alert(f"daily run SKIPPED {day} (no chain snapshot)", msg)
            _gap(day, "chain_snapshot_missing")
            return 0
        # outcome == "retry": the closes feed itself is dead -- fall through to
        # the zombie check below, which exits 1 so the tick retries.

    if (not args.smoke and snap is not None
            and zombie_check(len(market.skipped_closes), pulled_n,
                             market.chain_attempts, market.chains_ok,
                             run_cfg["zombie_threshold"])):
        # A16 skeptic F2: with a snapshot present, chain_fn never touches the
        # network -- every skipped chain is a candidate ABSENT from the 15:2x
        # snapshot, and no 17:00-23:30 retry can rebuild that snapshot. Letting
        # this fall into the zombie path below would retry-and-alert ~78 times
        # with a wrong diagnosis ("lapsed token"). Classify like the missing-
        # snapshot case instead: dead closes still retry, a holiday stays
        # quiet, and a real day is skipped once, truthfully labelled.
        outcome = snapshot_missing_outcome(market, range(pulled_n), obs,
                                           run_cfg["zombie_threshold"])
        if outcome == "holiday":
            print(f"{obs.date()}: not a trading session — holiday. No paper "
                  f"day was stepped; this is not a gap.")
            holiday_note(obs, smoke=args.smoke)
            return 0
        if outcome == "gap":
            day = str(obs.date())
            missing = [tk for tk, _ in market.skipped_chains]
            msg = (f"{day}: RTH chain snapshot is INCOMPLETE -- "
                   f"{market.chains_ok}/{market.chain_attempts} of today's "
                   f"candidates present (missing: {missing[:8]}). The snapshot "
                   f"cannot be rebuilt after 16:00, so by the A16 owner "
                   f"decision the day is SKIPPED, not retried. Marker written; "
                   f"day recorded as a gap.")
            print(f"SNAPSHOT INCOMPLETE -- {msg}")
            _alert(f"daily run SKIPPED {day} (snapshot incomplete)", msg)
            _gap(day, "chain_snapshot_incomplete", missing=missing,
                       chains_ok=market.chains_ok,
                       chain_attempts=market.chain_attempts)
            return 0
        # outcome == "retry": closes feed dead -- fall through, exit 1.

    if zombie_check(len(market.skipped_closes), pulled_n,
                    market.chain_attempts, market.chains_ok,
                    run_cfg["zombie_threshold"]):
        day = str(obs.date())
        msg = (f"{day}: price history failed for {len(market.skipped_closes)}/"
               f"{len(universe)} tickers; option chains {market.chains_ok}/"
               f"{market.chain_attempts} usable (threshold "
               f"{run_cfg['zombie_threshold']:.0%}). No state was touched and no "
               f"completion marker was written -- the next tick will retry. "
               f"Likely a lapsed Schwab token or a Schwab outage.")
        print(f"ZOMBIE RUN -- {msg}")
        _alert(f"daily run FAILED {day}", msg)
        _gap(day, "pull_failure",
                   skipped_closes=len(market.skipped_closes),
                   universe=len(universe),
                   chain_attempts=market.chain_attempts,
                   chains_ok=market.chains_ok)
        return 1

    # B8: short histories are visible, not silent (per-ticker lines printed by
    # LiveMarket; one summary here)
    trunc = getattr(market, "truncated_closes", [])
    if trunc:
        print(f"closes truncated for {len(trunc)} ticker(s): "
              f"{[t for t, _ in trunc[:8]]} -- entry-ineligible, not failures")

    if not is_trading_day(market, obs):
        # A holiday (or any weekday the exchange did not open). Nothing to do,
        # and this is NOT a gap -- so we exit 0 and let the tick write the
        # marker, which also stops the dead-man's switch reporting it as missed.
        print(f"{obs.date()}: not a trading session (no bar dated today for the "
              f"universe) — holiday or early close with no print. No paper day "
              f"was stepped; this is not a gap.")
        holiday_note(obs, smoke=args.smoke)
        return 0

    # A21c: judged only AFTER every no-session/no-snapshot classification --
    # a dead quote endpoint on a market holiday used to exit 1 here and
    # retry-alert "FAILED (held-leg marks)" all evening for a day with no
    # session. No session means there are no marks to fail.
    # A21-D2: a held-rows-absent day is degraded BY DESIGN and must step --
    # the snapshot is immutable after the window, so a 17:00 exit-1 here
    # would retry-spam all evening for a file no retry can rebuild (the
    # A16-F2 class). Its one alert already went out above.
    if not held_d2 and held_marks_failed(merged):
        day = str(obs.date())
        msg = (f"{day}: held-leg quote pull failed WHOLESALE "
               f"({merged['requested']} leg(s) requested, error="
               f"{merged['error']!r}, unquoted={merged['unquoted'][:6]}). "
               f"Every take-profit is suspended -- that is a failed run, not "
               f"a quiet one. No marker written; the next tick retries.")
        print(f"HELD MARKS FAILED -- {msg}")
        _alert(f"daily run FAILED {day} (held-leg marks)", msg)
        return 1

    print(f"\n=== paper day {obs.date()} — {len(accounts)} account(s), down-only gate ===")
    print(f"{'account':<10}{'trades':>7}{'open':>6}{'cash':>13}{'equity':>13}")
    stepped, failed, already = 0, [], []
    naked_all = set()   # A4: tickers whose covered call was unreachable, any account
    unsettled_all = {}  # A14: contract -> days-late, any account
    frozen_all = set()  # A10: corporate-action-frozen legs, any account
    gated_all = set()   # A3b/C16b: tickers whose covered call was refused, any account
    for (cap, n) in accounts:
        state, paths = loaded[(cap, n)]
        label = "_smoke" if args.smoke else account_label(cap, n)
        # isolate each account: one account's failure (bad chain, edge case) must not
        # abort the other 24 for the day (silent multi-account gap in an unattended run).
        try:
            os.makedirs(paths["dir"], exist_ok=True)
            # Re-running a day must not double-step it. The 17:00-ET retry window
            # fires every 5 min until a run succeeds, and a partial failure means
            # some accounts have already stepped -- without this guard the retry
            # appends a second snapshot for the same date (every account still
            # carries a duplicate 2026-07-24 row from before this guard existed).
            if already_stepped(paths["snapshots"], obs):
                already.append(label)
                print(f"{label:<10}{'--':>7}{len(state.positions):>6}"
                      f"{state.cash:>13,.0f}{'already':>13}")
                continue
            acfg = WheelConfig(ticker="SPY", starting_capital=float(cap), **FROZEN)
            r = paper_step(state, market, acfg, n, paths["trades"], paths["state"],
                           paths["snapshots"])
            naked_all |= {w[2] for w in r.warnings
                          if w[1] == "covered_call_unreachable"}
            frozen_all |= {str(w[2]) for w in r.warnings
                           if w[1] == "ca_confirmed_frozen"}
            gated_all |= {str(w[2]) for w in r.warnings
                          if w[1] == "call_gated_unclosable"}
            for key, late in collect_unsettled(r.warnings).items():
                unsettled_all[key] = max(unsettled_all.get(key, 0), late)
            stepped += 1
            print(f"{label:<10}{len(r.trades):>7}{len(state.positions):>6}"
                  f"{state.cash:>13,.0f}{r.equity:>13,.0f}")
        except Exception as e:
            failed.append(label)
            print(f"{label:<10} ERROR: {type(e).__name__}: {e} — skipped, others continue")

    day = str(obs.date())
    write_benchmark_row(accounts, market, day, smoke=args.smoke)   # C6
    if unsettled_all:
        # A14: the bound on the unsettleable-expiry refusal is ESCALATION --
        # one alert per day naming each stuck leg and how many days past
        # expiry it is. A leg stuck because its close never arrives (delisted
        # ticker, corporate action) can no longer wait in silence.
        legs = ", ".join(f"{k} ({v}d late)" for k, v in sorted(unsettled_all.items()))
        _alert(f"UNSETTLEABLE EXPIRY -- {len(unsettled_all)} leg(s) stuck",
                   f"{day}: expiry close still missing for: {legs}. The leg "
                   f"stays open and retries daily; if this repeats, suspect a "
                   f"delisting or corporate action (A10).")
    if frozen_all:
        # A10: ONE deduped alert, repeating daily until a human clears the
        # freeze (owner-manual by design -- a sticky wrong freeze is loud and
        # cheap; a wrongly-cleared split is the $42,750 class). Suspects
        # (one-day settlement deferrals) print via the generic path only.
        _alert(f"CORPORATE ACTION suspected -- {len(frozen_all)} leg(s) FROZEN",
               f"{day}: prior-session closes were RESTATED against stored "
               f"state for: {sorted(frozen_all)}. TP, settlement and covered "
               f"calls are suspended on these positions. Strike/shares/basis "
               f"need manual restatement, then clear ca_frozen in the state "
               f"file (A10; this alert repeats daily until cleared).")
    if not args.smoke:
        # A10b/C16b: conditions that log daily but never email escalate after
        # ESCALATION_DAYS consecutive stepped days. Both kinds are passed
        # EVERY stepped day (empty list = surface ran clean = streak resets).
        # Keys are global across accounts (market/data conditions, not
        # sizing). --smoke never touches the counter file (A23 class).
        # A21: on a day the held-quote surface did not actually run (D2 day,
        # or any pull error), OMIT unquoted_leg entirely -- an omitted kind
        # neither advances nor resets (escalations.py contract), so a failed
        # surface cannot launder a dead symbol's streak with a false "clean".
        seen = {"call_gated_unclosable": sorted(gated_all)}
        if merged["error"] is None:
            seen["unquoted_leg"] = merged["unquoted"]
        esc = update_escalations(day, seen)
        if esc:
            n_esc = sum(len(v) for v in esc.values())
            lines = []
            if esc.get("unquoted_leg"):
                lines.append("DEAD/UNQUOTED held legs (A10b -- reverse split, "
                             "symbol change or delisting; TP suspended): "
                             + ", ".join(f"{k} ({c}d)" for k, c in esc["unquoted_leg"]))
            if esc.get("call_gated_unclosable"):
                lines.append("covered call REFUSED daily, shares naked (C16b): "
                             + ", ".join(f"{k} ({c}d)" for k, c in
                                         esc["call_gated_unclosable"]))
            _alert(f"ESCALATION -- {n_esc} condition(s) at "
                   f"{ESCALATION_DAYS}+ consecutive days",
                   f"{day}: " + " | ".join(lines) + ". Repeats daily while "
                   f"the condition persists.")
    if naked_all:
        # A4: ONE deduped alert for all 25 accounts (heavy overlap, avg 7.2x
        # replication) -- shares sitting naked must reach the owner's inbox,
        # not just a log line.
        _alert(f"shares uncovered -- covered call unreachable "
                   f"({len(naked_all)} ticker(s))",
                   f"{day}: basis floor sits above every listed call strike in "
                   f"the snapshot for {sorted(naked_all)}. Shares are held "
                   f"uncovered and the bot will retry each session (A4). If "
                   f"this repeats daily for one ticker, suspect a corporate "
                   f"action (A10).")
    # A run that stepped NOTHING is a failed run, whatever the pulls did. The old
    # unconditional `return 0` meant 25 exceptions still exited 0, so the tick
    # wrote .dailyran and the day was permanently recorded as traded.
    if stepped == 0 and not already:
        msg = (f"{day}: all {len(accounts)} accounts failed to step "
               f"({', '.join(failed[:6])}{'...' if len(failed) > 6 else ''}). "
               f"Market data pulled fine, so this is not a data outage -- check "
               f"disk space, memory, and the run log. No completion marker was "
               f"written; the next tick will retry.")
        print(f"ALL ACCOUNTS FAILED -- {msg}")
        _alert(f"daily run FAILED {day}", msg)
        _gap(day, "all_accounts_failed", accounts=len(accounts))
        return 1
    if failed:
        # Partial failure still completes the day for the other accounts, so we
        # do NOT fail the run (that would re-step the healthy ones). But those
        # accounts now have a hole, and silence is what this audit was about.
        msg = (f"{day}: {len(failed)}/{len(accounts)} accounts failed to step: "
               f"{', '.join(failed)}. The other {stepped} completed and the day "
               f"is marked done. The failed accounts have a GAP for this date -- "
               f"their equity curve will look continuous but is missing a day.")
        print(f"PARTIAL FAILURE -- {msg}")
        _alert(f"daily run PARTIAL {day} ({len(failed)} accounts)", msg)
        # C13: the old call passed date= twice (TypeError -- the hole went
        # undisclosed AND the exception escaped after the healthy accounts
        # stepped; post-D9 that meant an all-evening retry loop + a false
        # no_run gap at 23:45). Key the REAL date; if the day already has a
        # record (a 17:05 zombie attempt before a 17:35 partial retry), file
        # a correction -- the day was not fully missed, it partially stepped.
        if not _gap(day, "accounts_failed", accounts=failed):
            _correction(day, "accounts_failed", accounts=failed)
    return 0


def _smoke_probe_leg(market, obs):
    """E4: one synthetic held leg for --smoke's merge call. Strike = one grid
    step BELOW the pulled GDX chain's bottom put strike (a real listed
    contract the surveyed window does not carry -> the splice actually
    splices); expiry = the chain's nearest expiry. None when GDX's chain is
    absent (the probe is best-effort -- a dead chain pull is already loud)."""
    ch = market.chain("GDX", obs)
    if ch is None or not len(ch):
        return None
    puts = ch[ch["right"] == "P"]
    if not len(puts):
        return None
    expiry = pd.Timestamp(puts["expiry"].min())
    ks = sorted(float(k) for k in puts[puts["expiry"] == expiry]["strike"].unique())
    step = min((b - a for a, b in zip(ks, ks[1:])), default=1.0) or 1.0
    return {"ticker": "GDX", "smoke_probe": True,
            "short": {"contract": {"root": "GDX", "expiry": str(expiry),
                                   "strike": ks[0] - step, "right": "P"},
                      "contracts": 1, "credit": 0.0, "last_mid": 0.0}}


def _paths(capital, n, smoke=False):
    if smoke:
        d = in_state("accounts", "_smoke")
        return {"dir": d, "state": os.path.join(d, "state.json"),
                "trades": os.path.join(d, "trades.jsonl"),
                "snapshots": os.path.join(d, "snapshots.jsonl")}
    return account_paths(capital, n)


if __name__ == "__main__":
    sys.exit(main())
