"""Regressions for the 2026-07-29 deep-audit findings.

Every test here pins a defect that FAILED SILENTLY -- the system reported success
while losing a trading day, leaking a credential, or reporting a number it had no
basis for. They are grouped by the finding they pin.
"""
import datetime as dt
import json
import subprocess

import pytest

from live.run_daily import zombie_check
from live.health import check_day, marker_path
from live.gaps import recorded_dates


# --- F1 (critical): zombie_check was blind to option-chain failure -----------
# skipped-closes and skipped-chains come from different-sized populations, so a
# total chain outage (~5% of the universe) could never reach a 50% threshold.
# A dead day was stamped .dailyran and lost forever.

def test_zombie_on_total_chain_failure_even_though_closes_all_succeeded():
    """The 2026-07-24 outcome through the door the old gate could not cover."""
    assert zombie_check(skipped_closes=0, universe_size=547,
                        chain_attempts=28, chains_ok=0, threshold=0.5) is True


def test_zombie_on_majority_chain_failure():
    assert zombie_check(skipped_closes=0, universe_size=547,
                        chain_attempts=28, chains_ok=10, threshold=0.5) is True


def test_healthy_day_with_a_few_chain_failures_is_not_a_zombie():
    assert zombie_check(skipped_closes=3, universe_size=547,
                        chain_attempts=28, chains_ok=26, threshold=0.5) is False


def test_no_chain_candidates_is_not_a_zombie():
    """A day where no ticker passes the chop gate and nothing is held is a
    legitimately quiet day, not a failure."""
    assert zombie_check(skipped_closes=0, universe_size=547,
                        chain_attempts=0, chains_ok=0, threshold=0.5) is False


def test_closes_failure_still_trips_the_gate():
    assert zombie_check(skipped_closes=547, universe_size=547,
                        chain_attempts=0, chains_ok=0, threshold=0.5) is True


def test_closes_and_chain_failures_are_not_pooled():
    """Old bug's mirror image: pooling summed 260 close failures + 15 chain
    failures to 275/547 = 50.3% and threw away a day on which 287 tickers had
    perfectly good data. Judged separately, neither feed is over threshold:
    closes 260/547 = 47.5%, chains 15/100 = 15%."""
    assert zombie_check(skipped_closes=260, universe_size=547,
                        chain_attempts=100, chains_ok=85, threshold=0.5) is False
    # and the pooled ratio that used to trip really is over the line
    assert (260 + 15) / 547 > 0.5


def test_empty_universe_is_a_zombie():
    assert zombie_check(skipped_closes=0, universe_size=0,
                        chain_attempts=0, chains_ok=0, threshold=0.5) is True


# --- F3 (high): the dead-man's switch only ever looked at the current day ----
# A multi-day outage (reboot, stopped timer) was erased: each missed day's
# check never ran, and the next check only asked about "today".

def test_health_backfills_a_multi_day_outage(tmp_path):
    """Thu 2026-07-30 20:30 ET after an outage since Mon: Mon/Tue/Wed must all
    be recorded, not just today."""
    g = str(tmp_path / "g.jsonl")
    r = check_day(dt.datetime(2026, 7, 30, 23, 50), str(tmp_path), g)
    assert r == "gap_recorded"
    got = recorded_dates(g)
    for d in ("2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30"):
        assert d in got, f"{d} missing -- a multi-day outage was erased"


def test_health_lookback_skips_weekends_and_completed_days(tmp_path):
    g = str(tmp_path / "g.jsonl")
    # Mon 27 and Tue 28 completed; Wed 29 did not
    for d in ("2026-07-27", "2026-07-28"):
        open(marker_path(d, str(tmp_path)), "w").close()
    check_day(dt.datetime(2026, 7, 29, 23, 50), str(tmp_path), g)
    got = recorded_dates(g)
    assert "2026-07-29" in got
    assert "2026-07-27" not in got and "2026-07-28" not in got
    assert "2026-07-25" not in got and "2026-07-26" not in got   # Sat/Sun


def test_health_still_idempotent_across_repeat_ticks(tmp_path):
    g = str(tmp_path / "g.jsonl")
    check_day(dt.datetime(2026, 7, 30, 23, 50), str(tmp_path), g)
    n1 = len(recorded_dates(g))
    check_day(dt.datetime(2026, 7, 30, 23, 55), str(tmp_path), g)
    assert len(recorded_dates(g)) == n1


# --- F5 (high): a push timeout wrote the GitHub PAT into a synced log --------
# subprocess.TimeoutExpired stringifies the full argv, and the push URL carries
# the token. The generic except printed it, into a log inside data/live, which
# is itself force-pushed to GitHub.

def test_push_timeout_never_prints_the_token(tmp_path, capsys, monkeypatch):
    import live.sync as sync_mod
    TOKEN = "github_pat_11ABCDEFG_supersecretvalue"
    cfg = tmp_path / "git.json"
    cfg.write_text(json.dumps({"repo": "o/r", "token": TOKEN, "user": "u"}))
    state = tmp_path / "state"
    state.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=state, check=True)

    def boom(args, cwd):
        if args[0] == "push":
            raise subprocess.TimeoutExpired(cmd=["git"] + args, timeout=120)
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(sync_mod, "_git", boom)
    monkeypatch.setattr(sync_mod, "_staged_paths", lambda d: ["state.json"])
    assert sync_mod.sync_state(str(state), "msg", cfg_path=str(cfg)) is False
    out = capsys.readouterr().out
    assert TOKEN not in out, "PAT leaked into stdout -> log -> pushed to GitHub"
    assert "***" in out


def test_generic_exception_never_prints_the_token(tmp_path, capsys, monkeypatch):
    import live.sync as sync_mod
    TOKEN = "github_pat_11ABCDEFG_supersecretvalue"
    cfg = tmp_path / "git.json"
    cfg.write_text(json.dumps({"repo": "o/r", "token": TOKEN, "user": "u"}))
    state = tmp_path / "state"
    state.mkdir()

    def boom(args, cwd):
        raise RuntimeError(f"exploded while running git push https://x-access-token:{TOKEN}@github.com/o/r.git")

    monkeypatch.setattr(sync_mod, "_git", boom)
    assert sync_mod.sync_state(str(state), "msg", cfg_path=str(cfg)) is False
    assert TOKEN not in capsys.readouterr().out


# --- F: token-age nag keyed on mtime, which schwab-py rewrites every run -----
# The refresh token lapses at 7 days, but token.json's mtime resets on every
# access-token refresh, so the file was never more than minutes old and the
# warning could never fire. creation_timestamp is the real issue time.

def test_token_age_reads_creation_timestamp_not_mtime(tmp_path):
    from live.tokenage import token_age_days
    import time
    p = tmp_path / "token.json"
    p.write_text(json.dumps({"creation_timestamp": int(time.time()) - 6 * 86400,
                             "token": {"refresh_token": "x"}}))
    # file was JUST written, so mtime age is ~0; the real age is 6 days
    assert 5.9 < token_age_days(str(p)) < 6.1


def test_token_age_missing_file_returns_none(tmp_path):
    from live.tokenage import token_age_days
    assert token_age_days(str(tmp_path / "absent.json")) is None


def test_token_age_malformed_returns_none(tmp_path):
    from live.tokenage import token_age_days
    p = tmp_path / "token.json"
    p.write_text("{not json")
    assert token_age_days(str(p)) is None


# --- F: no market-holiday gate -- the bot fabricated a full paper day on -----
# Thanksgiving/Good Friday at the PRIOR session's stale closes, polluting the
# equity series with a phantom row.

class _FakeMkt:
    def __init__(self, closes):
        self._closes = closes


def _series(dates):
    import pandas as pd
    idx = pd.to_datetime(dates)
    return pd.Series([1.0] * len(idx), index=idx)


def test_holiday_is_not_a_trading_day():
    from live.run_daily import is_trading_day
    import pandas as pd
    # every ticker's last bar is the prior session; nothing dated the holiday
    prior = _series(["2026-11-24", "2026-11-25"])
    mkt = _FakeMkt({f"T{i}": prior for i in range(50)})
    assert is_trading_day(mkt, pd.Timestamp("2026-11-26")) is False


def test_real_session_is_a_trading_day():
    from live.run_daily import is_trading_day
    import pandas as pd
    s = _series(["2026-11-24", "2026-11-25", "2026-11-27"])
    mkt = _FakeMkt({f"T{i}": s for i in range(50)})
    assert is_trading_day(mkt, pd.Timestamp("2026-11-27")) is True


def test_partial_data_still_counts_as_a_session():
    """A handful of stale symbols must not make a real session look like a holiday."""
    from live.run_daily import is_trading_day
    import pandas as pd
    live_s = _series(["2026-11-24", "2026-11-27"])
    stale = _series(["2026-11-24"])
    closes = {f"L{i}": live_s for i in range(40)}
    closes.update({f"S{i}": stale for i in range(10)})
    assert is_trading_day(_FakeMkt(closes), pd.Timestamp("2026-11-27")) is True


def test_empty_market_is_not_a_trading_day():
    from live.run_daily import is_trading_day
    import pandas as pd
    assert is_trading_day(_FakeMkt({}), pd.Timestamp("2026-11-27")) is False


# --- F: secret_guard checked path strings only -------------------------------
# The paths it named live outside the synced tree and can never be staged, so
# the check could not fire. A secret inside a normally-named file was the real
# exposure -- e.g. a token echoed into a run log, which IS synced.

def test_secret_guard_catches_a_pat_inside_a_normal_file(tmp_path):
    from live.sync import secret_guard
    d = tmp_path / "state"
    d.mkdir()
    (d / "logs").mkdir()
    (d / "logs" / "2026-07-29.log").write_text(
        "pushing... https://x-access-token:github_pat_11ABCDEFGHIJKLMNOPQRSTUVWXYZ012345@github.com/o/r.git failed")
    (d / "clean.json").write_text('{"equity": 100000}')
    bad = secret_guard(["logs/2026-07-29.log", "clean.json"], str(d))
    assert bad == ["logs/2026-07-29.log"]


def test_secret_guard_catches_a_private_key_body(tmp_path):
    from live.sync import secret_guard
    d = tmp_path / "state"
    d.mkdir()
    (d / "notes.txt").write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\n")
    assert secret_guard(["notes.txt"], str(d)) == ["notes.txt"]


def test_secret_guard_catches_a_leaked_refresh_token(tmp_path):
    from live.sync import secret_guard
    d = tmp_path / "state"
    d.mkdir()
    (d / "oops.json").write_text('{"refresh_token": "abcdefghijklmnop"}')
    assert secret_guard(["oops.json"], str(d)) == ["oops.json"]


def test_secret_guard_passes_ordinary_state_files(tmp_path):
    from live.sync import secret_guard
    d = tmp_path / "state"
    d.mkdir()
    (d / "state.json").write_text('{"cash": 100000.0, "positions": []}')
    (d / "trades.jsonl").write_text('{"action": "SELL_PUT", "ticker": "GDX"}\n')
    (d / "gaps.jsonl").write_text('{"date": "2026-07-21", "reason": "no_run"}\n')
    assert secret_guard(["state.json", "trades.jsonl", "gaps.jsonl"], str(d)) == []


# --- F: gaps.jsonl was write-only -- nothing ever read it back ---------------

def test_gap_summary_reads_the_ledger(tmp_path):
    from live.gaps import append_gap, gap_summary
    p = str(tmp_path / "g.jsonl")
    append_gap("2026-07-23", "no_run", path=p)
    append_gap("2026-07-21", "no_run", path=p)
    append_gap("2026-07-24", "pull_failure", path=p, skipped_closes=547)
    g = gap_summary(p)
    assert g["count"] == 3
    assert g["dates"] == ["2026-07-21", "2026-07-23", "2026-07-24"]   # sorted
    assert set(g["reasons"]) == {"no_run", "pull_failure"}


def test_gap_summary_empty_when_no_ledger(tmp_path):
    from live.gaps import gap_summary
    g = gap_summary(str(tmp_path / "absent.jsonl"))
    assert g["count"] == 0 and g["dates"] == []
