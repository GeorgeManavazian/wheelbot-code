"""PortfolioState <-> JSON. Positions carry nested Contract dataclasses, so
(de)serialization is explicit. save_state is atomic (temp + os.replace) so a
crash mid-write can't corrupt a real portfolio."""
from __future__ import annotations
import json
import os
import tempfile
import pandas as pd
from src.engine_v2.options.chain import Contract
from src.engine_v2.options.portfolio import PortfolioState

_POS_SCALARS = ("ticker", "shares", "phase", "basis", "premium", "campaign", "last_spot")


def _contract_to_dict(c):
    return {"root": c.root, "expiry": pd.Timestamp(c.expiry).isoformat(),
            "strike": c.strike, "right": c.right}


def _contract_from_dict(d):
    return Contract(d["root"], pd.Timestamp(d["expiry"]), d["strike"], d["right"])


def _pos_to_dict(p):
    d = {k: p[k] for k in _POS_SCALARS}
    # A17: an order a fill model has working (JSON-plain dict). Absent == none;
    # written only when present so existing files and default-path saves are
    # byte-identical to before the field existed.
    if p.get("working_order") is not None:
        d["working_order"] = p["working_order"]
    # A10: freeze/watch survive the round trip or a corporate-action freeze
    # silently un-freezes overnight. Written only when present (last_ask
    # precedent -- legacy files byte-identical). recon_frozen is A8's
    # broker-vs-state divergence freeze (real-money mode), same lifecycle.
    # assigned_d is A9's covered-call deferral date -- it must survive the
    # snapshot-failed retry window's reload or the re-step sells same-day.
    for k in ("ca_frozen", "ca_watch", "recon_frozen", "assigned_d"):
        if p.get(k) is not None:
            d[k] = p[k]
    sh = p["short"]
    if sh is None:
        d["short"] = None
        return d
    out = {"contract": _contract_to_dict(sh["contract"]),
           "contracts": sh["contracts"], "credit": sh["credit"],
           "last_mid": sh["last_mid"]}
    # last_ask is what equity is marked from (owner decision B, 2026-07-31). It
    # is optional ONLY because legs opened before that change have none; once a
    # leg has one it must survive the round trip, or every run would reload the
    # stale mid and silently restore the defect.
    if "last_ask" in sh:
        out["last_ask"] = sh["last_ask"]
    # A17: partial-fill capacity, optional for the same reason as last_ask --
    # every existing state file lacks it and must keep loading. Original leg
    # size; absent == equals `contracts`.
    if "opened_contracts" in sh:
        out["opened_contracts"] = sh["opened_contracts"]
    # C1: the carried-mark discriminator -- dropping these on save would make
    # every reloaded leg look freshly marked.
    for k in ("mark_asof", "mark_quote_time"):
        if k in sh:
            out[k] = sh[k]
    d["short"] = out
    return d


def _pos_from_dict(d):
    p = {k: d[k] for k in _POS_SCALARS}
    if d.get("working_order") is not None:
        p["working_order"] = d["working_order"]
    for k in ("ca_frozen", "ca_watch", "recon_frozen", "assigned_d"):
        if d.get(k) is not None:
            p[k] = d[k]
    sh = d["short"]
    if sh is None:
        p["short"] = None
        return p
    out = {"contract": _contract_from_dict(sh["contract"]),
           "contracts": sh["contracts"], "credit": sh["credit"],
           "last_mid": sh["last_mid"]}
    if "last_ask" in sh:
        out["last_ask"] = sh["last_ask"]
    if "opened_contracts" in sh:
        out["opened_contracts"] = sh["opened_contracts"]
    for k in ("mark_asof", "mark_quote_time"):
        if k in sh:
            out[k] = sh[k]
    p["short"] = out
    return p


def state_to_dict(state) -> dict:
    d = {
        "cash": state.cash, "campaign": state.campaign,
        "days_flat": state.days_flat,
        "days_shares_uncovered": state.days_shares_uncovered,
        "prev_d": None if state.prev_d is None else pd.Timestamp(state.prev_d).isoformat(),
        "positions": [_pos_to_dict(p) for p in state.positions],
    }
    # A5: intraday closes must survive to the 17:00 reload or the same-day
    # anti-churn guard evaporates. Written only when non-empty (last_ask
    # precedent: existing files stay byte-identical).
    closed = getattr(state, "intraday_closed", None)
    if closed:
        d["intraday_closed"] = [
            {"date": pd.Timestamp(e["date"]).isoformat(),
             "contract": _contract_to_dict(e["contract"])} for e in closed]
    return d


def state_from_dict(d) -> PortfolioState:
    return PortfolioState(
        cash=d["cash"], positions=[_pos_from_dict(p) for p in d["positions"]],
        campaign=d["campaign"], days_flat=d["days_flat"],
        days_shares_uncovered=d["days_shares_uncovered"],
        prev_d=None if d["prev_d"] is None else pd.Timestamp(d["prev_d"]),
        intraday_closed=[{"date": pd.Timestamp(e["date"]),
                          "contract": _contract_from_dict(e["contract"])}
                         for e in d.get("intraday_closed", [])])


BACKUP_KEEP_DAYS = 7


def _day_boundary_backup(path, dirn):
    """D11: `.prev` is refreshed on EVERY save and run_intraday saves on each
    TP close, so by evening .prev is minutes old -- a valid-but-wrong EOD
    write destroyed the last good day-boundary state. The FIRST save of an ET
    day copies the state it found to `state.json.bak-<date>` (never touched
    again that day); backups older than BACKUP_KEEP_DAYS are pruned. These
    are recovery artifacts and deliberately sync to the mirror."""
    import datetime as _dt
    import glob
    import shutil
    from zoneinfo import ZoneInfo
    today = _dt.datetime.now(ZoneInfo("America/New_York")).date()
    bak = f"{path}.bak-{today}"
    if os.path.exists(path) and not os.path.exists(bak):
        shutil.copy2(path, bak)
        _fsync_path(bak)
    cutoff = today - _dt.timedelta(days=BACKUP_KEEP_DAYS)
    for old in glob.glob(f"{path}.bak-*"):
        try:
            d = _dt.date.fromisoformat(old.rsplit(".bak-", 1)[1])
        except ValueError:
            continue
        if d < cutoff:
            try:
                os.remove(old)
            except OSError:
                pass


def _fsync_path(p):
    try:
        fd = os.open(p, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def save_state(state, path):
    """Atomic + durable + backups. temp-write -> fsync -> day-boundary dated
    backup (first save of the day, D11) -> keep the prior state.json as .prev
    (crash-window recovery) -> os.replace -> fsync the DIRECTORY (the rename
    itself is not durable across power loss without it). A crash or power
    loss can't leave a half-written or truncated state.json; yesterday's
    day-boundary state survives the whole day, not just one save."""
    import shutil
    d = state_to_dict(state)
    dirn = os.path.dirname(path) or "."
    os.makedirs(dirn, exist_ok=True)
    _day_boundary_backup(path, dirn)
    if os.path.exists(path):
        shutil.copy2(path, path + ".prev")
        _fsync_path(path + ".prev")
    fd, tmp = tempfile.mkstemp(dir=dirn, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(d, f, indent=2)
        f.flush()
        os.fsync(f.fileno())     # durable content before the atomic rename
    os.replace(tmp, path)
    try:
        dfd = os.open(dirn, os.O_RDONLY)
        try:
            os.fsync(dfd)        # durable RENAME, not just content
        finally:
            os.close(dfd)
    except OSError:
        pass


def load_state(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return state_from_dict(json.load(f))
