# Live Bot VPS Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the live paper bot off the sleeping MacBook onto the always-on Oracle VPS, and fix the three silent-failure modes that let 2026-07-21, 07-23 and 07-24 pass unnoticed.

**Architecture:** Four new pure-Python modules in `live/` (gap ledger, alerts, health check, git sync), one behavior change in `run_daily.py` (zombie gate), and a single systemd timer firing a dispatcher shell script every 5 minutes that does ET gating in shell — the same structure `scripts/wheelbot_loop.sh` already proves correct, minus the `while true` loop that dies on reboot.

**Tech Stack:** Python 3.12 (`.venv-live`), pytest, systemd timers, git over HTTPS with a fine-grained PAT, `smtplib` over Gmail SMTP.

## Global Constraints

- **Strategy logic is FROZEN.** No change to `FROZEN` params, the chop gate, `step_one_day`, TP rules, or any file under `src/engine_v2/`. This migration is P&L-neutral by construction.
- **Data-only.** No order-placement code anywhere. `scripts/schwab/schwab_client.py`'s no-orders boundary holds.
- **No backfill, ever.** Schwab has no historical option-chain endpoint (`chain_frame` uses `from_date=today`). Missed days are disclosed via `gaps.jsonl`, never reconstructed.
- Tests run from the repo root with `PYTHONPATH=. .venv-live/bin/python -m pytest`.
- Secrets live in `~/.wheelbot/` and `~/.schwab/`, chmod 600, **never** committed.
- Zombie threshold default: `0.5` (half the universe failing = failed run).
- Server: `ubuntu@129.80.185.142`, key `~/.ssh/wheelbot.key`, repo path `/home/ubuntu/etf-bot`.

---

### Task 1: Gap ledger

**Files:**
- Create: `live/gaps.py`
- Test: `live/tests/test_gaps.py`

**Interfaces:**
- Consumes: nothing
- Produces: `recorded_dates(path=GAPS_PATH) -> set[str]`, `append_gap(date, reason, path=GAPS_PATH, **extra) -> bool`, constant `GAPS_PATH = "data/live/gaps.jsonl"`

- [ ] **Step 1: Write the failing test**

```python
import json
from live.gaps import append_gap, recorded_dates


def test_append_and_read_back(tmp_path):
    p = str(tmp_path / "gaps.jsonl")
    assert recorded_dates(p) == set()
    assert append_gap("2026-07-21", "no_run", path=p) is True
    assert recorded_dates(p) == {"2026-07-21"}


def test_idempotent_per_date(tmp_path):
    p = str(tmp_path / "gaps.jsonl")
    assert append_gap("2026-07-21", "no_run", path=p) is True
    assert append_gap("2026-07-21", "no_run", path=p) is False
    assert append_gap("2026-07-21", "pull_failure", path=p) is False
    with open(p) as f:
        assert len([ln for ln in f if ln.strip()]) == 1


def test_extra_fields_persisted(tmp_path):
    p = str(tmp_path / "gaps.jsonl")
    append_gap("2026-07-24", "pull_failure", path=p, skipped=547, universe=547)
    rec = json.loads(open(p).read().strip())
    assert rec["reason"] == "pull_failure"
    assert rec["skipped"] == 547 and rec["universe"] == 547


def test_missing_file_is_empty_not_error(tmp_path):
    assert recorded_dates(str(tmp_path / "nope.jsonl")) == set()


def test_corrupt_line_skipped_not_fatal(tmp_path):
    p = str(tmp_path / "gaps.jsonl")
    with open(p, "w") as f:
        f.write('{"date": "2026-07-21", "reason": "no_run"}\n')
        f.write("not json at all\n")
        f.write('{"no_date_key": true}\n')
    assert recorded_dates(p) == {"2026-07-21"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_gaps.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'live.gaps'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Append-only ledger of trading days the bot failed to trade.

A missed day is NOT recoverable: Schwab exposes no historical option-chain
endpoint, so what a chain looked like on a past date cannot be retrieved and
that day's entries/assignments/expiries are gone. Reconstructing them from any
other source would inject look-ahead bias. So we record and disclose instead.

Any forward-test result read off this store must report its gap days alongside
it, the same way the STATUS notes disclose contaminated runs."""
from __future__ import annotations
import json
import os

GAPS_PATH = "data/live/gaps.jsonl"


def recorded_dates(path: str = GAPS_PATH) -> set:
    """Every date already in the ledger. Missing file -> empty set. Corrupt
    lines are skipped, never fatal -- a half-written line must not blind the
    idempotency check and cause duplicate records forever after."""
    out = set()
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.add(json.loads(line)["date"])
                except (ValueError, KeyError, TypeError):
                    continue
    except FileNotFoundError:
        return out
    return out


def append_gap(date, reason: str, path: str = GAPS_PATH, **extra) -> bool:
    """Record one missed day. Idempotent PER DATE (not per reason): a day is
    either traded or it isn't, and the health check may run repeatedly on the
    same day. Returns False when the date was already recorded."""
    date = str(date)
    if date in recorded_dates(path):
        return False
    rec = {"date": date, "reason": reason}
    rec.update(extra)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_gaps.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add live/gaps.py live/tests/test_gaps.py
git commit -m "feat(live): gap ledger for days the bot could not trade"
```

---

### Task 2: Email alerts

**Files:**
- Create: `live/alerts.py`
- Test: `live/tests/test_alerts.py`

**Interfaces:**
- Consumes: nothing
- Produces: `send_alert(subject, body, path=ALERTS_PATH) -> bool`, `load_alert_config(path=ALERTS_PATH) -> dict | None`, constant `ALERTS_PATH`

- [ ] **Step 1: Write the failing test**

```python
import json
import pytest
from live import alerts


def _cfg(tmp_path):
    p = tmp_path / "alerts.json"
    p.write_text(json.dumps({
        "smtp_host": "smtp.example.com", "smtp_port": 587,
        "username": "u@example.com", "password": "pw",
        "from_addr": "u@example.com", "to_addr": "u@example.com"}))
    return str(p)


def test_missing_config_returns_none(tmp_path):
    assert alerts.load_alert_config(str(tmp_path / "nope.json")) is None


def test_send_alert_without_config_returns_false_and_does_not_raise(tmp_path):
    assert alerts.send_alert("subj", "body", path=str(tmp_path / "nope.json")) is False


def test_send_alert_success(tmp_path, monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"], sent["port"] = host, port
        def starttls(self): sent["tls"] = True
        def login(self, u, p): sent["login"] = (u, p)
        def send_message(self, m): sent["msg"] = m
        def quit(self): sent["quit"] = True

    monkeypatch.setattr(alerts.smtplib, "SMTP", FakeSMTP)
    assert alerts.send_alert("token stale", "6 days", path=_cfg(tmp_path)) is True
    assert sent["host"] == "smtp.example.com" and sent["tls"] is True
    assert sent["msg"]["Subject"] == "Wheel Bot: token stale"
    assert sent["msg"]["To"] == "u@example.com"


def test_smtp_failure_is_swallowed(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("network unreachable")
    monkeypatch.setattr(alerts.smtplib, "SMTP", boom)
    # must NOT raise -- a broken mailer can never abort a trading run
    assert alerts.send_alert("subj", "body", path=_cfg(tmp_path)) is False


def test_malformed_config_returns_none(tmp_path):
    p = tmp_path / "alerts.json"
    p.write_text("{not json")
    assert alerts.load_alert_config(str(p)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_alerts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'live.alerts'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Email alerts for the unattended bot. Config at ~/.wheelbot/alerts.json
(chmod 600, outside the repo -- same pattern as ~/.schwab/config.json).

INVARIANT: send_alert never raises. A dead mailer must never be the reason a
trading run aborts; it degrades to a printed line in the run log."""
from __future__ import annotations
import json
import os
import smtplib
from email.message import EmailMessage

ALERTS_PATH = os.path.expanduser("~/.wheelbot/alerts.json")


def load_alert_config(path: str = ALERTS_PATH):
    """The alert config dict, or None when absent/unreadable/malformed."""
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None
    return cfg if isinstance(cfg, dict) else None


def send_alert(subject: str, body: str, path: str = ALERTS_PATH) -> bool:
    """Send one alert. True on success, False on any failure (never raises).
    Subject is prefixed 'Wheel Bot: ' so inbox filters have a stable handle."""
    cfg = load_alert_config(path)
    if cfg is None:
        print(f"[alert skipped -- no config at {path}] {subject}")
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = f"Wheel Bot: {subject}"
        msg["From"] = cfg["from_addr"]
        msg["To"] = cfg["to_addr"]
        msg.set_content(body)
        srv = smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=30)
        srv.starttls()
        srv.login(cfg["username"], cfg["password"])
        srv.send_message(msg)
        srv.quit()
        return True
    except Exception as e:                      # noqa: BLE001 -- deliberate
        print(f"[alert FAILED {type(e).__name__}: {e}] {subject}")
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_alerts.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add live/alerts.py live/tests/test_alerts.py
git commit -m "feat(live): email alerts that never abort a run on failure"
```

---

### Task 3: Zombie-run gate in the daily runner

**Files:**
- Modify: `live/config.py` (add `zombie_threshold` to DEFAULTS + validation)
- Modify: `live/run_daily.py` (gate after market construction; `main()` returns an exit code)
- Test: `live/tests/test_zombie_gate.py`
- Test: `live/tests/test_config.py` (extend)

**Interfaces:**
- Consumes: `live.gaps.append_gap`, `live.alerts.send_alert`
- Produces: `live.run_daily.zombie_check(skipped, universe_size, threshold) -> bool` (True = run is a zombie, abort)

- [ ] **Step 1: Write the failing test**

```python
import json
import pytest
from live.run_daily import zombie_check
from live.config import load_run_config


def test_zombie_check_boundaries():
    # below threshold -> fine
    assert zombie_check(skipped=200, universe_size=547, threshold=0.5) is False
    # exactly at threshold -> zombie (>= is the rule)
    assert zombie_check(skipped=274, universe_size=547, threshold=0.5) is True
    # total failure -> zombie
    assert zombie_check(skipped=547, universe_size=547, threshold=0.5) is True
    # a handful of delisted names -> fine
    assert zombie_check(skipped=3, universe_size=547, threshold=0.5) is False


def test_zombie_check_empty_universe_is_zombie():
    # an empty universe means the pull produced nothing at all
    assert zombie_check(skipped=0, universe_size=0, threshold=0.5) is True


def test_config_exposes_zombie_threshold_default(tmp_path):
    cfg = load_run_config(str(tmp_path / "absent.json"))
    assert cfg["zombie_threshold"] == 0.5


def test_config_zombie_threshold_override(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"n": 5, "capital": 100000, "zombie_threshold": 0.8}))
    assert load_run_config(str(p))["zombie_threshold"] == 0.8


def test_config_rejects_out_of_range_threshold(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"zombie_threshold": 1.5}))
    with pytest.raises(ValueError):
        load_run_config(str(p))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_zombie_gate.py -v`
Expected: FAIL with `ImportError: cannot import name 'zombie_check'`

- [ ] **Step 3: Write minimal implementation**

In `live/config.py`, change `DEFAULTS` and add validation:

```python
DEFAULTS = {"n": 5, "capital": 100_000.0, "zombie_threshold": 0.5}
```

and inside `load_run_config`, after the existing `capital` handling:

```python
    if "zombie_threshold" in raw:
        cfg["zombie_threshold"] = float(raw["zombie_threshold"])
    if not (0.0 < cfg["zombie_threshold"] <= 1.0):
        raise ValueError(f"{path}: zombie_threshold must be in (0, 1], "
                         f"got {cfg['zombie_threshold']}")
```

In `live/run_daily.py`, add the predicate near the top (after `_trade_row`):

```python
def zombie_check(skipped: int, universe_size: int, threshold: float) -> bool:
    """True when this run pulled so little data that it is a FAILED run, not a
    quiet one. 2026-07-24 is the case this exists for: all 547 tickers failed,
    the run exited 0, booked no trades, and wrote its 'done' marker -- so a
    totally dead day was permanently recorded as complete and became
    unrecoverable. A zombie run must write no marker so the next tick retries."""
    if universe_size <= 0:
        return True
    return (skipped / universe_size) >= threshold
```

In `main()`, immediately after the `market.skipped` print block, replace it with:

```python
    run_cfg = load_run_config()
    if market.skipped:
        print(f"skipped {len(market.skipped)} tickers (pull failures): "
              f"{[s[0] for s in market.skipped][:8]}")
    if zombie_check(len(market.skipped), len(universe), run_cfg["zombie_threshold"]):
        day = str(obs.date())
        msg = (f"{day}: pull failed for {len(market.skipped)}/{len(universe)} "
               f"tickers (threshold {run_cfg['zombie_threshold']:.0%}). No state "
               f"was touched and no completion marker was written -- the next "
               f"tick will retry. Likely a lapsed Schwab token or an outage.")
        print(f"ZOMBIE RUN -- {msg}")
        send_alert(f"daily run FAILED {day}", msg)
        append_gap(day, "pull_failure",
                   skipped=len(market.skipped), universe=len(universe))
        return 1
```

Add the imports at the top of `run_daily.py`:

```python
from live.alerts import send_alert
from live.gaps import append_gap
from live.config import load_run_config
```

And make `main()` return `0` at the end of the account loop, then change the entrypoint:

```python
if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_zombie_gate.py live/tests/test_config.py live/tests/test_run_daily.py -v`
Expected: all pass (existing `test_config.py` and `test_run_daily.py` must stay green)

- [ ] **Step 5: Commit**

```bash
git add live/config.py live/run_daily.py live/tests/test_zombie_gate.py
git commit -m "fix(live): failed pulls no longer mark the day complete

2026-07-24 failed all 547 pulls, exited 0 and wrote its .dailyran marker,
permanently recording a dead day as done. A run that loses >= half the
universe now returns nonzero, alerts, logs a gap, and leaves no marker so
the next tick retries."
```

---

### Task 4: Health check (dead-man's switch)

**Files:**
- Create: `live/health.py`
- Create: `live/run_health.py`
- Test: `live/tests/test_health.py`

**Interfaces:**
- Consumes: `live.gaps.append_gap`, `live.alerts.send_alert`
- Produces: `marker_path(date, logs_dir) -> str`, `check_day(now_et, logs_dir, gaps_path, cutoff_hhmm=2015) -> str` returning one of `"not_weekday"`, `"too_early"`, `"ok"`, `"already_recorded"`, `"gap_recorded"`

- [ ] **Step 1: Write the failing test**

```python
import datetime as dt
from live.health import marker_path, check_day
from live.gaps import recorded_dates


def _et(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm)


def test_marker_path_shape(tmp_path):
    assert marker_path("2026-07-24", str(tmp_path)).endswith(".dailyran-2026-07-24")


def test_weekend_is_not_a_gap(tmp_path):
    # 2026-07-25 is a Saturday
    r = check_day(_et(2026, 7, 25, 21, 0), str(tmp_path), str(tmp_path / "g.jsonl"))
    assert r == "not_weekday"


def test_before_cutoff_does_nothing(tmp_path):
    # 2026-07-24 is a Friday, 18:00 is before the 20:15 cutoff
    r = check_day(_et(2026, 7, 24, 18, 0), str(tmp_path), str(tmp_path / "g.jsonl"))
    assert r == "too_early"


def test_marker_present_means_ok(tmp_path):
    open(marker_path("2026-07-24", str(tmp_path)), "w").close()
    r = check_day(_et(2026, 7, 24, 20, 30), str(tmp_path), str(tmp_path / "g.jsonl"))
    assert r == "ok"


def test_missing_marker_records_gap(tmp_path):
    g = str(tmp_path / "g.jsonl")
    r = check_day(_et(2026, 7, 24, 20, 30), str(tmp_path), g)
    assert r == "gap_recorded"
    assert recorded_dates(g) == {"2026-07-24"}


def test_running_twice_records_once(tmp_path):
    g = str(tmp_path / "g.jsonl")
    assert check_day(_et(2026, 7, 24, 20, 30), str(tmp_path), g) == "gap_recorded"
    assert check_day(_et(2026, 7, 24, 22, 0), str(tmp_path), g) == "already_recorded"
    with open(g) as f:
        assert len([ln for ln in f if ln.strip()]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'live.health'`

- [ ] **Step 3: Write minimal implementation**

`live/health.py`:

```python
"""Dead-man's switch. Every other alert needs a living process to fire; this
one fires BECAUSE nothing happened. It is the only check that catches the bot
being entirely dead -- the 2026-07-21 / 07-23 case, where no run occurred at
all and nothing anywhere noticed for weeks.

Runs after the daily retry window closes (20:00 ET), so a day that merely
retried late is not misreported as missed."""
from __future__ import annotations
import os

LOGS_DIR = "data/live/logs"
CUTOFF_HHMM = 2015          # 20:15 ET -- after the 17:00-20:00 retry window


def marker_path(date, logs_dir: str = LOGS_DIR) -> str:
    """The .dailyran-<date> completion marker the daily runner writes on success."""
    return os.path.join(logs_dir, f".dailyran-{date}")


def check_day(now_et, logs_dir: str = LOGS_DIR, gaps_path=None,
              cutoff_hhmm: int = CUTOFF_HHMM) -> str:
    """Decide whether `now_et`'s date is a missed trading day, and record it.

    Returns one of: "not_weekday", "too_early", "ok", "already_recorded",
    "gap_recorded". Caller alerts on "gap_recorded" only -- re-alerting on
    "already_recorded" would email every 5 minutes until midnight."""
    from live.gaps import append_gap, GAPS_PATH
    if gaps_path is None:
        gaps_path = GAPS_PATH
    if now_et.weekday() >= 5:
        return "not_weekday"
    if now_et.hour * 100 + now_et.minute < cutoff_hhmm:
        return "too_early"
    day = now_et.strftime("%Y-%m-%d")
    if os.path.exists(marker_path(day, logs_dir)):
        return "ok"
    return "gap_recorded" if append_gap(day, "no_run", path=gaps_path) \
        else "already_recorded"
```

`live/run_health.py`:

```python
"""Health-check entrypoint, fired by the 5-minute tick. Sub-second no-op on
weekends, before the cutoff, or on a day that already completed.

  PYTHONPATH=. .venv-live/bin/python live/run_health.py
"""
from __future__ import annotations
import datetime as dt
import sys
from zoneinfo import ZoneInfo

from live.health import check_day
from live.alerts import send_alert

ET = ZoneInfo("America/New_York")


def main() -> int:
    now = dt.datetime.now(ET)
    result = check_day(now.replace(tzinfo=None))
    if result == "gap_recorded":
        day = now.strftime("%Y-%m-%d")
        msg = (f"No successful daily run for {day} -- the retry window closed "
               f"at 20:00 ET with no completion marker. That day's entries, "
               f"assignments and expiries did not happen and CANNOT be "
               f"backfilled (Schwab has no historical option-chain endpoint). "
               f"Logged to data/live/gaps.jsonl.\n\n"
               f"Check: is the VPS up? Has the Schwab token lapsed?")
        print(f"GAP -- {msg}")
        send_alert(f"MISSED TRADING DAY {day}", msg)
    else:
        print(f"health {now:%Y-%m-%d %H:%M %Z}: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_health.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add live/health.py live/run_health.py live/tests/test_health.py
git commit -m "feat(live): dead-man's switch for days with no run at all

2026-07-21 and 07-23 were weekdays with no run and no marker; nothing
noticed. This fires because nothing happened, which no other alert can do."
```

---

### Task 5: State sync to GitHub

**Files:**
- Create: `live/sync.py`
- Test: `live/tests/test_sync.py`

**Interfaces:**
- Consumes: nothing (reads `~/.wheelbot/git.json`)
- Produces: `secret_guard(paths) -> list[str]` (returns offending paths), `sync_state(state_dir, message, cfg_path=GIT_CFG) -> bool`

- [ ] **Step 1: Write the failing test**

```python
import json
import subprocess
import pytest
from live.sync import secret_guard, sync_state


def test_secret_guard_flags_token_paths():
    bad = secret_guard([
        "accounts/5k_N1/state.json",
        ".schwab/token.json",
        "gaps.jsonl",
    ])
    assert bad == [".schwab/token.json"]


def test_secret_guard_flags_wheelbot_dir_and_pem():
    bad = secret_guard([
        ".wheelbot/alerts.json",
        ".wheelbot/git.json",
        "oci_api_key.pem",
        "id_rsa",
        "trades.jsonl",
    ])
    assert set(bad) == {".wheelbot/alerts.json", ".wheelbot/git.json",
                        "oci_api_key.pem", "id_rsa"}


def test_secret_guard_clean_list_is_empty():
    assert secret_guard(["state.json", "trades.jsonl", "snapshots.jsonl",
                         "gaps.jsonl", "logs/2026-07-28.log"]) == []


def test_sync_state_missing_config_returns_false(tmp_path):
    assert sync_state(str(tmp_path), "msg",
                      cfg_path=str(tmp_path / "absent.json")) is False


def test_sync_state_aborts_when_secret_staged(tmp_path, monkeypatch):
    cfg = tmp_path / "git.json"
    cfg.write_text(json.dumps({"repo": "o/r", "token": "t", "user": "u"}))
    state = tmp_path / "state"
    state.mkdir()
    (state / "token.json").write_text("{}")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=state, check=True)
    subprocess.run(["git", "add", "-A"], cwd=state, check=True)
    monkeypatch.setattr("live.sync._staged_paths", lambda d: [".schwab/token.json"])
    assert sync_state(str(state), "msg", cfg_path=str(cfg)) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_sync.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'live.sync'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Push data/live/ to a private GitHub repo so the Mac's dashboard can read
current state without the VPS being reachable.

Direction is one-way: VPS -> GitHub -> Mac. The Mac never writes, so the VPS is
the unambiguous source of truth and there is no merge case to reason about.

INVARIANT: no credential ever leaves this machine. secret_guard() runs against
the actual staged paths before every push and aborts on a match."""
from __future__ import annotations
import json
import os
import subprocess

GIT_CFG = os.path.expanduser("~/.wheelbot/git.json")

# substrings that must never appear in a staged path
_SECRET_MARKERS = (".schwab", ".wheelbot", "token.json", "oci_api_key",
                   ".pem", "id_rsa", "credentials")


def secret_guard(paths) -> list:
    """Every path that looks like a credential. Empty list == safe to push."""
    return [p for p in paths
            if any(m in p for m in _SECRET_MARKERS)]


def _git(args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True, timeout=120)


def _staged_paths(state_dir: str) -> list:
    r = _git(["diff", "--cached", "--name-only"], state_dir)
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def load_git_config(path: str = GIT_CFG):
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None
    return cfg if isinstance(cfg, dict) else None


def sync_state(state_dir: str, message: str, cfg_path: str = GIT_CFG) -> bool:
    """Commit and push state_dir. True on success. Never raises -- a failed
    push must not abort a trading run; the VPS keeps authoritative state and
    the Mac catches up on the next successful push."""
    cfg = load_git_config(cfg_path)
    if cfg is None:
        print(f"[sync skipped -- no config at {cfg_path}]")
        return False
    try:
        if not os.path.isdir(os.path.join(state_dir, ".git")):
            _git(["init", "-q", "-b", "main"], state_dir)
            _git(["config", "user.email", "wheelbot@localhost"], state_dir)
            _git(["config", "user.name", "wheelbot"], state_dir)

        _git(["add", "-A"], state_dir)

        offenders = secret_guard(_staged_paths(state_dir))
        if offenders:
            print(f"[sync ABORTED -- credential in staged paths: {offenders}]")
            _git(["reset"], state_dir)
            return False

        if not _staged_paths(state_dir):
            return True                       # nothing changed; not a failure

        c = _git(["commit", "-m", message], state_dir)
        if c.returncode != 0 and "nothing to commit" not in (c.stdout + c.stderr):
            print(f"[sync commit failed] {c.stderr.strip()[:300]}")
            return False

        url = (f"https://x-access-token:{cfg['token']}@github.com/"
               f"{cfg['repo']}.git")
        p = _git(["push", url, "main"], state_dir)
        if p.returncode != 0:
            # scrub the token out of any error text before it reaches a log
            err = p.stderr.replace(cfg["token"], "***")
            print(f"[sync push failed] {err.strip()[:300]}")
            return False
        return True
    except Exception as e:                     # noqa: BLE001 -- deliberate
        print(f"[sync FAILED {type(e).__name__}: {e}]")
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_sync.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add live/sync.py live/tests/test_sync.py
git commit -m "feat(live): one-way state sync VPS -> GitHub with credential guard"
```

---

### Task 6: Tick dispatcher + systemd unit

**Files:**
- Create: `scripts/wheelbot_tick.sh`
- Create: `deploy/wheelbot.service`
- Create: `deploy/wheelbot.timer`
- Test: manual verification on the server (documented below)

**Interfaces:**
- Consumes: `live/run_daily.py`, `live/run_intraday.py`, `live/run_health.py`
- Produces: a single systemd timer firing every 5 minutes

**Design note:** one timer, not three. The ET gating stays in shell exactly as
`wheelbot_loop.sh` already does it (proven correct), which sidesteps systemd
`OnCalendar` timezone handling entirely. The `while true` loop is gone —
systemd restarts the timer on boot, which the loop never did.

**The one real fix in the port:** the old loop ran `touch "$marker"`
unconditionally after `run_daily.py`. That is what made 2026-07-24 permanent.
The marker is now written **only on exit 0**.

- [ ] **Step 1: Write the dispatcher**

`scripts/wheelbot_tick.sh`:

```bash
#!/bin/bash
# One tick of the live bot, fired every 5 minutes by systemd (wheelbot.timer).
# All market-hours gating is done here in ET, so the system clock can stay UTC.
# Replaces scripts/wheelbot_loop.sh, whose `while true` loop died on reboot
# with nothing to restart it.
set -uo pipefail
REPO="/home/ubuntu/etf-bot"
cd "$REPO" || exit 1
PY="$REPO/.venv-live/bin/python"
LOGDIR="$REPO/data/live/logs"
mkdir -p "$LOGDIR"

ET(){ TZ=America/New_York date "$@"; }
log(){ echo "$(ET '+%Y-%m-%d %H:%M:%S ET') $*" >> "$LOGDIR/tick.log"; }

DOW=$(ET +%u)
HM=$((10#$(ET +%H%M)))
TODAY=$(ET +%Y-%m-%d)
MARKER="$LOGDIR/.dailyran-$TODAY"

# --- EOD daily run: weekdays, 17:00-20:00 ET, once per day -----------------
# Fires on every tick inside the window; the marker stops the second success.
# That repetition IS the retry mechanism for a failed run.
if [ "$DOW" -le 5 ] && [ "$HM" -ge 1700 ] && [ "$HM" -le 2000 ] && [ ! -f "$MARKER" ]; then
  log "daily run start"
  PYTHONPATH="$REPO" "$PY" live/run_daily.py >> "$LOGDIR/$TODAY.log" 2>&1
  RC=$?
  log "daily run exit $RC"
  if [ "$RC" -eq 0 ]; then
    touch "$MARKER"          # ONLY on success -- a failed run must retry
    PYTHONPATH="$REPO" "$PY" -c \
      "from live.sync import sync_state; sync_state('data/live', 'eod $TODAY')" \
      >> "$LOGDIR/$TODAY.log" 2>&1
  fi
fi

# --- Intraday exit manager: weekdays 9:30-16:00 ET -------------------------
if [ "$DOW" -le 5 ] && [ "$HM" -ge 930 ] && [ "$HM" -le 1600 ]; then
  OUT=$(PYTHONPATH="$REPO" "$PY" live/run_intraday.py 2>&1)
  echo "$OUT" >> "$LOGDIR/intraday-$TODAY.log"
  # push only when a trade was actually booked -- not on the ~78 daily no-ops
  if echo "$OUT" | grep -qE "closed [1-9][0-9]* at TP"; then
    PYTHONPATH="$REPO" "$PY" -c \
      "from live.sync import sync_state; sync_state('data/live', 'intraday $TODAY')" \
      >> "$LOGDIR/intraday-$TODAY.log" 2>&1
  fi
fi

# --- Health check: weekdays after 20:15 ET ---------------------------------
if [ "$DOW" -le 5 ] && [ "$HM" -ge 2015 ]; then
  PYTHONPATH="$REPO" "$PY" live/run_health.py >> "$LOGDIR/health-$TODAY.log" 2>&1
fi

exit 0
```

- [ ] **Step 2: Write the systemd units**

`deploy/wheelbot.service`:

```ini
[Unit]
Description=Wheel Bot tick (EOD run, intraday exits, health check)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/etf-bot
ExecStart=/bin/bash /home/ubuntu/etf-bot/scripts/wheelbot_tick.sh
TimeoutStartSec=900
```

`deploy/wheelbot.timer`:

```ini
[Unit]
Description=Fire the Wheel Bot tick every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Verify the gating logic locally before deploying**

Run: `bash -n scripts/wheelbot_tick.sh`
Expected: no output (syntax OK)

- [ ] **Step 4: Commit**

```bash
git add scripts/wheelbot_tick.sh deploy/wheelbot.service deploy/wheelbot.timer
git commit -m "feat(deploy): 5-minute systemd tick replaces the while-true loop

The old loop died on reboot with nothing to restart it, and wrote its
completion marker unconditionally. The marker is now written only on exit 0."
```

---

### Task 7: Deploy to the server

**Files:**
- Modify: none in-repo. This task runs commands against `ubuntu@129.80.185.142`.

**Interfaces:**
- Consumes: everything from Tasks 1-6
- Produces: a running, timer-driven bot on the VPS with state seeded from the Mac

- [ ] **Step 1: Copy code and state to the server**

```bash
ssh -i ~/.ssh/wheelbot.key ubuntu@129.80.185.142 'mkdir -p /home/ubuntu/etf-bot'
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot
rsync -az --delete -e "ssh -i ~/.ssh/wheelbot.key" \
  live/ ubuntu@129.80.185.142:/home/ubuntu/etf-bot/live/
rsync -az --delete -e "ssh -i ~/.ssh/wheelbot.key" \
  src/ ubuntu@129.80.185.142:/home/ubuntu/etf-bot/src/
rsync -az --delete -e "ssh -i ~/.ssh/wheelbot.key" \
  scripts/schwab/ ubuntu@129.80.185.142:/home/ubuntu/etf-bot/scripts/schwab/
rsync -az -e "ssh -i ~/.ssh/wheelbot.key" \
  scripts/wheelbot_tick.sh ubuntu@129.80.185.142:/home/ubuntu/etf-bot/scripts/
rsync -az -e "ssh -i ~/.ssh/wheelbot.key" \
  data/live/ ubuntu@129.80.185.142:/home/ubuntu/etf-bot/data/live/
rsync -az -e "ssh -i ~/.ssh/wheelbot.key" \
  ~/.schwab/ ubuntu@129.80.185.142:/home/ubuntu/.schwab/
```

- [ ] **Step 2: Build the venv**

```bash
ssh -i ~/.ssh/wheelbot.key ubuntu@129.80.185.142 'bash -s' <<'EOF'
cd /home/ubuntu/etf-bot
python3.12 -m venv .venv-live
.venv-live/bin/pip install -q --upgrade pip
.venv-live/bin/pip install -q pandas numpy pyarrow schwab-py pytest
chmod 600 /home/ubuntu/.schwab/token.json /home/ubuntu/.schwab/config.json
chmod +x scripts/wheelbot_tick.sh
.venv-live/bin/python -c "import pandas, schwab; print('deps OK')"
EOF
```

- [ ] **Step 3: Run the test suite on the server**

```bash
ssh -i ~/.ssh/wheelbot.key ubuntu@129.80.185.142 \
  'cd /home/ubuntu/etf-bot && PYTHONPATH=. .venv-live/bin/python -m pytest live/tests -q 2>&1 | tail -15'
```
Expected: all pass. A failure here means the migration is not equivalent — stop and fix before continuing.

- [ ] **Step 4: Install and start the timer**

```bash
ssh -i ~/.ssh/wheelbot.key ubuntu@129.80.185.142 'bash -s' <<'EOF'
sudo cp /home/ubuntu/etf-bot/deploy/wheelbot.service /etc/systemd/system/
sudo cp /home/ubuntu/etf-bot/deploy/wheelbot.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wheelbot.timer
systemctl list-timers wheelbot.timer --no-pager
EOF
```
Expected: timer listed as active with a NEXT time within 5 minutes.

- [ ] **Step 5: Commit the deploy record**

```bash
git add -A
git commit -m "chore(deploy): live bot deployed to Oracle VPS 129.80.185.142"
```

---

### Task 8: Cutover and verification

**Files:** none. This task is verification and the Mac-side switch.

**BLOCKING: requires the owner** for the Schwab login (step 2).

- [ ] **Step 1: Stop the Mac loop and remove the dead launchd agents**

```bash
pkill -f wheelbot_loop.sh
launchctl unload ~/Library/LaunchAgents/com.wheelbot.daily.plist 2>/dev/null
launchctl unload ~/Library/LaunchAgents/com.wheelbot.intraday.plist 2>/dev/null
rm -f ~/Library/LaunchAgents/com.wheelbot.{daily,intraday}.plist
ps aux | grep -c "[w]heelbot_loop.sh"   # expect 0
```

- [ ] **Step 2: Schwab login on the server (OWNER)**

```bash
ssh -i ~/.ssh/wheelbot.key ubuntu@129.80.185.142
cd /home/ubuntu/etf-bot
PYTHONPATH=. .venv-live/bin/python scripts/schwab/schwab_login.py
```
It prints a URL. Open it in a laptop browser, log in, authorize, paste the
final redirected URL back into the SSH session.

- [ ] **Step 3: Smoke test — proves Schwab connectivity end to end**

```bash
ssh -i ~/.ssh/wheelbot.key ubuntu@129.80.185.142 \
  'cd /home/ubuntu/etf-bot && PYTHONPATH=. .venv-live/bin/python live/run_daily.py --smoke'
```
Expected: a `_smoke` account line with a real equity number, and **no**
`skipped 3 tickers` line. Any pull failure here means the token did not take.

- [ ] **Step 4: Backfill the three known historical gaps**

```bash
ssh -i ~/.ssh/wheelbot.key ubuntu@129.80.185.142 'cd /home/ubuntu/etf-bot && PYTHONPATH=. .venv-live/bin/python - <<PY
from live.gaps import append_gap
append_gap("2026-07-21", "no_run", detected="reconstructed-from-logs")
append_gap("2026-07-23", "no_run", detected="reconstructed-from-logs")
append_gap("2026-07-24", "pull_failure", skipped=547, universe=547,
           detected="reconstructed-from-logs")
print(open("data/live/gaps.jsonl").read())
PY'
```
Expected: three records. These are the days already lost; the ledger must
carry them or every future report silently overstates coverage.

- [ ] **Step 5: Verify the first real tick**

```bash
ssh -i ~/.ssh/wheelbot.key ubuntu@129.80.185.142 \
  'sudo systemctl start wheelbot.service; sleep 20; tail -20 /home/ubuntu/etf-bot/data/live/logs/tick.log'
```
Expected: a tick logged. Outside market hours and outside 17:00-20:00 ET it
correctly does nothing — that is a pass, not a failure.

- [ ] **Step 6: Point the Mac dashboard at synced state**

```bash
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot
git clone https://github.com/GeorgeManavazian/wheelbot-state.git /tmp/wbstate && \
  ls /tmp/wbstate
```
Expected: the synced state files. Wire the dashboard's read path to a local
clone that is `git pull`ed, rather than the now-stale `data/live/`.

- [ ] **Step 7: Commit and update project status**

```bash
git add -A
git commit -m "chore: cutover to VPS complete; Mac retains dashboard only"
```

---

## Self-Review

**Spec coverage check:**

| Spec section | Task |
|---|---|
| Host / free-tier VPS | done pre-plan (server live) |
| What moves | Task 7 Step 1 |
| Scheduling (systemd) | Task 6 |
| Zombie-run detection | Task 3 |
| The unrecoverable half / gaps.jsonl | Tasks 1, 4; backfill in Task 8 Step 4 |
| State sync to the Mac | Task 5; wired in Task 6; verified Task 8 Step 6 |
| Alerting (4 triggers) | Task 2 (module); trigger 1+2 in Task 3; trigger 4 in Task 4; **trigger 3 (token age ≥ 6 days) — see gap below** |
| Security | Task 7 Step 2 (chmod), Task 5 (secret guard); sshd hardening already done |
| Error handling | Tasks 2, 5 (never-raise invariants), Task 3 (zombie path) |
| Testing | every task; migration equivalence in Task 7 Step 3 |
| Cutover | Task 8 |

**Gap found and closed:** alert trigger 3 (Schwab token age ≥ 6 days) had no
task. The old `wheelbot_daily.sh:18-22` did this with `osascript`, which does
not exist on Linux. Adding it to Task 6's dispatcher:

Insert into `scripts/wheelbot_tick.sh`, immediately before the EOD block:

```bash
# --- weekly Schwab login reminder: warn at 6 days, once per day ------------
TOKEN="$HOME/.schwab/token.json"
NAG="$LOGDIR/.tokennag-$TODAY"
if [ -f "$TOKEN" ] && [ ! -f "$NAG" ] && [ "$DOW" -le 5 ] && [ "$HM" -ge 1700 ]; then
  AGE=$(( ( $(date +%s) - $(stat -c %Y "$TOKEN") ) / 86400 ))
  if [ "$AGE" -ge 6 ]; then
    PYTHONPATH="$REPO" "$PY" -c \
      "from live.alerts import send_alert; send_alert('Schwab login due', 'The Schwab refresh token is ${AGE}d old and lapses at 7 days. SSH in and re-run scripts/schwab/schwab_login.py, or the bot goes blind.')" \
      >> "$LOGDIR/tick.log" 2>&1
    touch "$NAG"
  fi
fi
```

Note `stat -c %Y` (GNU/Linux), not `stat -f %m` (BSD/macOS) as in the old script.

**Placeholder scan:** none found — every step has runnable code or an exact command.

**Type consistency:** `append_gap`/`recorded_dates` signatures match between
Tasks 1, 3, 4 and 8. `send_alert(subject, body, path)` consistent across Tasks
2, 3, 4 and 6. `sync_state(state_dir, message, cfg_path)` consistent between
Tasks 5 and 6. `check_day` return strings match between `health.py` and
`run_health.py`.
