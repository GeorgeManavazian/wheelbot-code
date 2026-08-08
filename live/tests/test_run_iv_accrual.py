"""Runner wiring. Everything external is monkeypatched at the module that owns
it (main() re-imports inside the function, so call-time attribute patches land);
no network, no real store. Same harness shape as test_run_chain_snapshot.py.
"""
import datetime as dt
import sys
import types
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import live.run_iv_accrual as ria
from live.run_iv_accrual import (IV_OPEN, IV_CLOSE, accrual_window_open,
                                 frozen_cell_on_grid, pull_failed,
                                 save_still_rth)

ET = ZoneInfo("America/New_York")


def _at(h, m, day=17):
    return dt.datetime(2026, 7, day, h, m, tzinfo=ET)


def _clockseq(*stamps):
    stamps = list(stamps)

    class _DT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return stamps.pop(0) if len(stamps) > 1 else stamps[0]
    return _DT


# ---- predicates -----------------------------------------------------------

def test_window_is_weekday_rth_only():
    assert accrual_window_open(_at(15, 30)) is True          # Friday
    assert accrual_window_open(_at(15, 19)) is False
    assert accrual_window_open(_at(17, 0)) is False, "17:00 is the defect, not a fallback"
    assert accrual_window_open(_at(15, 30, day=18)) is False, "Saturday"
    assert IV_OPEN == 1520 and IV_CLOSE == 1555


def test_save_deadline_refuses_post_close_quotes():
    assert save_still_rth(_at(16, 4)) is True
    assert save_still_rth(_at(16, 6)) is False


def test_pull_failure_is_measured_on_the_feed_not_the_yield():
    assert pull_failed(547, 12, 0.5) is True
    assert pull_failed(547, 541, 0.5) is False
    assert pull_failed(0, 0, 0.5) is True, "nothing attempted is not a quiet day here"


def test_frozen_must_be_a_grid_member():
    """Otherwise the store fills happily with six series, none of which is the
    strategy being traded."""
    grid = [(0.30, 11), (0.20, 7)]
    assert frozen_cell_on_grid({"put_delta": 0.30, "target_dte": 11}, grid) is True
    assert frozen_cell_on_grid({"put_delta": 0.25, "target_dte": 9}, grid) is False


def test_the_real_frozen_is_on_the_real_grid():
    from live.iv_accrual import ACCRUAL_GRID
    from live.run_daily import FROZEN
    assert frozen_cell_on_grid(FROZEN, ACCRUAL_GRID) is True


# ---- main() wiring --------------------------------------------------------

@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Returns a helper that runs main() with everything external stubbed."""
    def _run(*, universe=("GDX", "SPY"), snapshot={"GDX": None},
             prior=None, pulled_prior=None, frames=None, start=None, done=None,
             threshold=0.5, frozen=None, observations=None):
        import live.chain_store as cs
        import live.config as lc
        import live.data as ld
        import live.iv_store as ivs
        import live.run_daily as rd
        import live.universe as lu

        start = start or _at(15, 30)
        monkeypatch.setattr(ria.dt, "datetime", _clockseq(start, done or start))
        # argparse's main() calls parse_args() with no arguments, so it reads
        # the real process sys.argv -- under `python -m pytest ... -q` that is
        # the test path and -q flag, which argparse rejects as unrecognized.
        # Same fix as test_run_chain_snapshot.py's _wire(): pin argv to just
        # the prog name so main()'s own --force flag is the only thing parsed.
        monkeypatch.setattr(sys, "argv", ["run_iv_accrual.py"])
        fake = types.ModuleType("schwab_client")
        client_calls = []
        # get_client() is an OAuth/token round-trip in production -- real work,
        # not a free stub. Recording calls lets refusal tests prove the gate
        # runs BEFORE that work, not just before the chain pulls.
        fake.get_client = lambda: client_calls.append(1) or object()
        monkeypatch.setitem(sys.modules, "schwab_client", fake)
        monkeypatch.setattr(lu, "UNIVERSE", list(universe))
        monkeypatch.setattr(cs, "load_chain_snapshot", lambda obs: snapshot)
        monkeypatch.setattr(lc, "load_run_config",
                            lambda *a, **k: {"zombie_threshold": threshold})
        if frozen is not None:
            monkeypatch.setattr(rd, "FROZEN", frozen)
        monkeypatch.setattr(ivs, "load_iv_day", lambda obs: prior)
        monkeypatch.setattr(ivs, "tickers_pulled", lambda obs: pulled_prior)

        saved = {}
        monkeypatch.setattr(ivs, "save_iv_day",
                            lambda obs, records, pulled_at, pulled_tickers=(),
                            **kw:
                            saved.update(obs=obs, records=records,
                                         pulled_at=pulled_at,
                                         pulled_tickers=list(pulled_tickers))
                            or str(tmp_path / "iv.json"))

        pulled = []

        def _frame(client, tk, obs_date=None):
            pulled.append(tk)
            if frames is not None and tk in frames:
                out = frames[tk]
                if isinstance(out, Exception):
                    raise out
                return out
            raise RuntimeError(f"{tk} boom")

        monkeypatch.setattr(ld, "otm_put_frame", _frame)

        def _obs(tk, frame, obs):
            if observations is not None:
                out = observations(tk)
                if isinstance(out, Exception):
                    raise out
                return out
            return [{"ticker": tk, "iv": 0.3}]

        monkeypatch.setattr(ria, "observations_for", _obs)
        rc = ria.main()
        return rc, saved, pulled, client_calls
    return _run


def test_refuses_before_the_trading_snapshot_exists(wired):
    rc, saved, pulled, client_calls = wired(snapshot=None)
    assert rc != 0
    assert saved == {}, "nothing saved"
    assert pulled == [], "no API calls -- trading eats first"
    assert client_calls == [], \
        "get_client() (an OAuth round-trip) must not run before the gate either"


def test_refuses_outside_the_window(wired):
    rc, saved, pulled, _ = wired(start=_at(17, 0))
    assert rc != 0 and saved == {}
    assert pulled == [], \
        "no chain pulls -- deleting the window gate must not stay green just " \
        "because every ticker also fails on an unset frame"


def test_refuses_when_frozen_is_off_grid(wired):
    rc, saved, pulled, _ = wired(frozen={"put_delta": 0.25, "target_dte": 9})
    assert rc != 0
    assert saved == {} and pulled == []


def test_happy_path_saves_and_exits_zero(wired):
    df = pd.DataFrame({"x": [1]})
    rc, saved, pulled, _ = wired(frames={"GDX": df, "SPY": df})
    assert rc == 0
    assert sorted(pulled) == ["GDX", "SPY"]
    assert {r["ticker"] for r in saved["records"]} == {"GDX", "SPY"}


def test_wholesale_pull_failure_exits_one(wired):
    rc, saved, pulled, _ = wired(frames={})       # every ticker raises
    assert rc != 0
    assert sorted(pulled) == ["GDX", "SPY"], "it tried all of them"


def test_a_wholesale_failure_day_still_saves_what_answered(wired):
    """Schwab degrades at 15:30 and most chains 503. The failure ratio is over
    the threshold, so this is an outage and exits 1 with no marker -- but the
    chains that DID answer are RTH observations that cannot be re-derived
    (no historical chain endpoint), and the retries at 15:35/15:40 hit the same
    outage until the window closes at 15:55. Discarding them loses them
    permanently."""
    df = pd.DataFrame({"x": [1]})
    good = [f"OK{i}" for i in range(4)]
    bad = [f"BAD{i}" for i in range(6)]           # 6/10 fail, over the 0.5 thr
    rc, saved, pulled, _ = wired(universe=tuple(good + bad),
                                 frames={tk: df for tk in good})
    assert rc != 0, "an outage must still exit nonzero (no marker, retries)"
    assert {r["ticker"] for r in saved["records"]} == set(good), \
        "the chains that answered must not be thrown away"
    assert sorted(saved["pulled_tickers"]) == good


def test_a_wholesale_failure_past_the_deadline_still_saves_nothing(wired):
    """Saving on failure must not sneak past the RTH guard: post-close quotes
    are never stamped RTH, outage or not."""
    df = pd.DataFrame({"x": [1]})
    rc, saved, _, _ = wired(universe=("GDX", "SPY", "IWM", "QQQ"),
                            frames={"GDX": df},
                            start=_at(15, 50), done=_at(16, 30))
    assert rc != 0 and saved == {}


def test_an_unexpected_raise_in_observations_for_does_not_kill_the_day(wired):
    """observations_for used to sit OUTSIDE the try guarding the pull, so one
    unexpected row killed the process before any save and lost every ticker
    pulled so far in the pass."""
    df = pd.DataFrame({"x": [1]})
    rc, saved, pulled, _ = wired(
        universe=("GDX", "SPY", "IWM"),
        frames={"GDX": df, "SPY": df, "IWM": df},
        observations=lambda tk: (KeyError("delta") if tk == "SPY"
                                 else [{"ticker": tk, "iv": 0.3}]))
    assert sorted(pulled) == ["GDX", "IWM", "SPY"], "the run continued"
    assert {r["ticker"] for r in saved["records"]} == {"GDX", "IWM"}
    assert "SPY" not in saved["pulled_tickers"], \
        "counted exactly like a pull failure, so a later tick retries it"
    assert rc != 0


def test_resume_does_not_repull_a_zero_yield_ticker(wired):
    """IWM's chain pulled fine and had no in-band expiry, so it wrote no
    record. It is DONE. Keying the resume on records re-pulled it on every
    remaining in-window tick -- the design promises 47 calls, not 547."""
    df = pd.DataFrame({"x": [1]})
    rc, saved, pulled, _ = wired(universe=("GDX", "SPY", "IWM"),
                                 frames={"SPY": df},
                                 prior=[{"ticker": "GDX", "iv": 0.2}],
                                 pulled_prior=["GDX", "IWM"])
    assert pulled == ["SPY"], "IWM yielded nothing but was already pulled"
    assert sorted(saved["pulled_tickers"]) == ["GDX", "IWM", "SPY"], \
        "the roster must carry forward, or the next tick re-pulls them"


def test_each_record_carries_its_own_pull_time(wired):
    """A carried-forward record keeps the time it was really measured; only
    this pass's records get this pass's clock."""
    df = pd.DataFrame({"x": [1]})
    earlier = "2026-07-17T15:28:00-04:00"
    rc, saved, _, _ = wired(frames={"SPY": df},
                            prior=[{"ticker": "GDX", "iv": 0.2,
                                    "pulled_at": earlier}],
                            pulled_prior=["GDX"],
                            start=_at(15, 40))
    by_tk = {r["ticker"]: r for r in saved["records"]}
    assert by_tk["GDX"]["pulled_at"] == earlier, \
        "an earlier pass's real pull time must not be overwritten"
    assert by_tk["SPY"]["pulled_at"] == _at(15, 40).isoformat()


def test_partial_failure_saves_the_good_records_and_exits_one(wired):
    """3-ticker universe, one bad chain: attempted=3 ok=2 gives a 1/3 chain
    failure ratio, under the 0.5 threshold -- so this reaches the PARTIAL
    branch (save what came in, exit 1) rather than the wholesale-FAILED one."""
    df = pd.DataFrame({"x": [1]})
    rc, saved, pulled, _ = wired(universe=("GDX", "SPY", "IWM"),
                                 frames={"GDX": df, "SPY": df})
    assert rc != 0
    assert sorted(pulled) == ["GDX", "IWM", "SPY"]
    assert {r["ticker"] for r in saved["records"]} == {"GDX", "SPY"}, \
        "the two good chains are saved even though IWM failed"


def test_resume_skips_tickers_already_pulled_today(wired):
    df = pd.DataFrame({"x": [1]})
    rc, saved, pulled, _ = wired(frames={"SPY": df},
                                 prior=[{"ticker": "GDX", "iv": 0.2}],
                                 pulled_prior=["GDX"])
    assert pulled == ["SPY"], "GDX was already done"
    assert {r["ticker"] for r in saved["records"]} == {"GDX", "SPY"}, \
        "prior records are carried forward, not dropped"


def test_a_slow_pull_past_the_deadline_is_discarded(wired):
    df = pd.DataFrame({"x": [1]})
    rc, saved, _, _ = wired(frames={"GDX": df, "SPY": df},
                            start=_at(15, 50), done=_at(16, 30))
    assert rc != 0
    assert saved == {}, "post-close quotes must never be stamped RTH"
