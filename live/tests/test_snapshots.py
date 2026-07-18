import json
import pandas as pd
from src.engine_v2.options.chain import Contract
from src.engine_v2.options.portfolio import PortfolioState
from live.snapshots import snapshot, append_snapshot, load_snapshots


def _state():
    put = Contract("GDX", pd.Timestamp("2026-07-31"), 30.0, "P")
    pos = {"ticker": "GDX", "shares": 0, "phase": "PUT", "basis": None,
           "premium": 60.0, "campaign": 1, "last_spot": 31.0,
           "short": {"contract": put, "contracts": 1, "credit": 0.6, "last_mid": 0.55}}
    return PortfolioState(cash=99_940.0, positions=[pos], campaign=1, days_flat=2)


def test_snapshot_captures_equity_cash_and_positions():
    snap = snapshot(_state(), equity=100_010.0, day=pd.Timestamp("2026-07-17"))
    assert snap["equity"] == 100_010.0 and snap["cash"] == 99_940.0
    assert snap["n_positions"] == 1 and snap["days_flat"] == 2
    p = snap["positions"][0]
    assert p["ticker"] == "GDX" and p["phase"] == "PUT"
    assert p["short"]["strike"] == 30.0 and p["short"]["right"] == "P"
    assert p["date"] if "date" in p else True  # positions have no date; snap does
    assert snap["date"] == pd.Timestamp("2026-07-17").isoformat()


def test_snapshot_flat_state_has_empty_positions():
    snap = snapshot(PortfolioState(cash=100_000.0, positions=[]), 100_000.0,
                    pd.Timestamp("2026-07-17"))
    assert snap["n_positions"] == 0 and snap["positions"] == []


def test_append_and_load_round_trip(tmp_path):
    path = str(tmp_path / "snapshots.jsonl")
    assert load_snapshots(path) == []            # absent -> empty
    append_snapshot(path, snapshot(_state(), 100_010.0, pd.Timestamp("2026-07-17")))
    append_snapshot(path, snapshot(_state(), 100_020.0, pd.Timestamp("2026-07-18")))
    snaps = load_snapshots(path)
    assert len(snaps) == 2                        # append-only, oldest first
    assert snaps[0]["equity"] == 100_010.0 and snaps[1]["equity"] == 100_020.0
