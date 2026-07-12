import pandas as pd
from dashboard import wheel_history as wh

def test_log_load_clear(tmp_path, monkeypatch):
    p = tmp_path / "runs.csv"
    monkeypatch.setattr(wh, "RUNS_PATH", str(p))
    assert wh.load_runs().empty
    wh.log_run({"ts": "2026-07-12T10:00", "pnl": 100.0, "sharpe": 0.5})
    wh.log_run({"ts": "2026-07-12T11:00", "pnl": 200.0, "sharpe": 0.6})
    df = wh.load_runs()
    assert len(df) == 2
    assert df.iloc[0]["ts"] == "2026-07-12T11:00"   # newest first
    wh.clear_runs()
    assert wh.load_runs().empty
