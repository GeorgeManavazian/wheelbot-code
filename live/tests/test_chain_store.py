import json

import pandas as pd

from live.chain_store import save_chain_snapshot, load_chain_snapshot
from live.data import chain_from_json, _CHAIN_COLS

OBS = pd.Timestamp("2026-07-17")
OC = json.load(open("live/fixtures/option_chain_gdx_puts.json"))


def _roundtrip(tmp_path, chains, obs=OBS):
    p = str(tmp_path / "snap.json")
    save_chain_snapshot(obs, chains, pulled_at="2026-07-17T15:45:00-04:00", path=p)
    return p, load_chain_snapshot(obs, path=p)


def test_roundtrip_preserves_engine_shape(tmp_path):
    df = chain_from_json(OC, OBS)
    _, loaded = _roundtrip(tmp_path, {"GDX": df})
    got = loaded["GDX"]
    assert list(got.columns) == _CHAIN_COLS
    # the engine filters on `date` and `expiry` as Timestamps
    assert got["date"].iloc[0] == OBS
    assert isinstance(got["expiry"].iloc[0], pd.Timestamp)
    pd.testing.assert_frame_equal(got, df)


def test_empty_snapshot_is_present_not_missing(tmp_path):
    # "no candidates today" must stay distinguishable from "window failed"
    _, loaded = _roundtrip(tmp_path, {})
    assert loaded == {}


def test_wrong_obs_date_is_never_served(tmp_path):
    """Yesterday's chains must never come back as today's -- the 2026-07-24
    failure mode (a day stepped against multi-day-old marks) but through the
    snapshot store."""
    df = chain_from_json(OC, OBS)
    p, _ = _roundtrip(tmp_path, {"GDX": df}, obs=OBS)
    assert load_chain_snapshot(OBS + pd.Timedelta(days=1), path=p) is None


def test_missing_or_corrupt_file_returns_none(tmp_path):
    assert load_chain_snapshot(OBS, path=str(tmp_path / "absent.json")) is None
    p = tmp_path / "corrupt.json"
    p.write_text("{not json")
    assert load_chain_snapshot(OBS, path=str(p)) is None
    p2 = tmp_path / "notdict.json"
    p2.write_text('["nope"]')
    assert load_chain_snapshot(OBS, path=str(p2)) is None


def test_deep_corrupt_chains_return_none(tmp_path):
    """Skeptic F1: 'corrupt -> None' must hold one level down. A garbage-but-
    valid-JSON body used to raise out of the DataFrame constructor on every
    17:00 retry tick, or (rows missing engine columns) load 'successfully' as
    a NaN-filled frame served straight to the engine."""
    p = str(tmp_path / "s.json")
    for chains in ("notdict",                  # chains not a dict
                   {"GDX": "rows"},            # rows not a list
                   {"GDX": [1, 2]},            # rows not dicts
                   {"GDX": [{"strike": 1.0}]}  # row missing engine columns
                   ):
        with open(p, "w") as f:
            json.dump({"obs": "2026-07-17", "pulled_at": "t",
                       "chains": chains}, f)
        assert load_chain_snapshot(OBS, path=p) is None, chains


def test_missing_delta_stays_numeric(tmp_path):
    df = chain_from_json(OC, OBS)
    df = df.copy()
    df.loc[df.index[0], "delta"] = float("nan")
    _, loaded = _roundtrip(tmp_path, {"GDX": df})
    # object dtype here makes select_contract's `.abs()` raise per-account
    assert loaded["GDX"]["delta"].dtype.kind == "f"
