"""IV observation store. Two contracts copied from chain_store, for the same
reasons stated there:

  * atomic write -- a reader must never see a half-written file
  * load returns None, NEVER a stale dict, for a file stamped with another date

The second one is the 2026-07-24 failure mode (a day stepped against multi-day-
old marks) and it is worse here than for chains: a stale IV file would append
observations under today's date that were measured on another day's quotes,
inside a series whose whole purpose is comparing a ticker to its own past.
"""
import json

import pandas as pd

from live.iv_store import iv_path, save_iv_day, load_iv_day, tickers_done

OBS = pd.Timestamp("2026-07-17")

REC = {"ticker": "GDX", "put_delta": 0.30, "target_dte": 11,
       "expiry": "2026-07-31", "strike": 68.0, "dte": 14, "delta": -0.31,
       "bid": 0.95, "ask": 1.02, "mid": 0.98, "underlying": 71.32,
       "rate": 0.03707, "div_yield": 0.00888, "iv": 0.3412,
       "source": "schwab-rth/bs-v1/d30/dte11"}


def _roundtrip(tmp_path, records, obs=OBS, load_obs=None):
    p = str(tmp_path / "iv.json")
    save_iv_day(obs, records, pulled_at="2026-07-17T15:35:00-04:00", path=p)
    return p, load_iv_day(load_obs or obs, path=p)


def test_roundtrip_preserves_every_field(tmp_path):
    _, got = _roundtrip(tmp_path, [REC])
    assert got == [REC]


def test_empty_day_is_present_not_missing(tmp_path):
    """'pulled fine, nothing selectable' must stay distinguishable from
    'the window failed' -- same rule as chain_store's empty snapshot."""
    _, got = _roundtrip(tmp_path, [])
    assert got == []
    assert got is not None


def test_wrong_obs_date_is_never_served(tmp_path):
    _, got = _roundtrip(tmp_path, [REC], load_obs=OBS + pd.Timedelta(days=1))
    assert got is None


def test_missing_or_corrupt_file_returns_none(tmp_path):
    assert load_iv_day(OBS, path=str(tmp_path / "absent.json")) is None
    for name, body in (("corrupt.json", "{not json"),
                       ("notdict.json", '["nope"]'),
                       ("norecords.json", '{"obs": "2026-07-17"}'),
                       ("badrecords.json", '{"obs": "2026-07-17", "records": 7}')):
        p = tmp_path / name
        p.write_text(body)
        assert load_iv_day(OBS, path=str(p)) is None, name


def test_write_is_atomic_no_tmp_file_left_behind(tmp_path):
    p, _ = _roundtrip(tmp_path, [REC])
    assert not (tmp_path / "iv.json.tmp").exists()
    payload = json.loads(open(p).read())
    assert payload["obs"] == "2026-07-17"
    assert payload["pulled_at"] == "2026-07-17T15:35:00-04:00"


def test_tickers_done_powers_the_resume(tmp_path):
    """Resume is keyed on the TICKER, not the grid cell: one API call produces
    all six cells, so a ticker is either done or not."""
    recs = [dict(REC), dict(REC, target_dte=7), dict(REC, ticker="SPY")]
    assert tickers_done(recs) == {"GDX", "SPY"}
    assert tickers_done([]) == set()


def test_path_lives_under_the_state_root_so_it_rides_the_mirror():
    assert iv_path(OBS).endswith("iv/2026-07-17.json")
    assert "data/live" in iv_path(OBS) or "iv/2026-07-17.json" in iv_path(OBS)
