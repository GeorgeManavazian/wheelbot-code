# tests/engine_v2/gate/test_verdict.py
import json
import pathlib
import stat
import numpy as np
import pandas as pd
import pytest
from src.engine_v2.gate.verdict import compute_verdict, persist_verdict

def _mk_regime(n):
    idx = pd.date_range("2010-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "regime_trend": ["bull"] * n,
        "regime_vol": ["calm"] * n,
        "regime_rate": ["falling"] * n,
    }, index=idx)

def test_coinflip_verdict_shelf():
    rng = np.random.default_rng(0)
    n = 300
    trm = pd.DataFrame(
        rng.normal(0, 0.01, (n, 8)),
        index=pd.date_range("2010-01-01", periods=n, freq="B"),
        columns=[f"t{i}" for i in range(8)],
    )
    v = compute_verdict(trm, _mk_regime(n), calmar_overall=0.0)
    assert v["shelf"] and not v["pass"]

def test_verdict_insufficient_when_k_lt_5():
    rng = np.random.default_rng(0)
    n = 200
    trm = pd.DataFrame(rng.normal(0, 0.01, (n, 2)),
                       index=pd.date_range("2010-01-01", periods=n, freq="B"),
                       columns=["a", "b"])
    v = compute_verdict(trm, _mk_regime(n), calmar_overall=1.5)
    assert v["notes"].get("insufficient_trials") is True
    assert v["pass"] is False

def test_persist_verdict_seals(tmp_path):
    verdict = {"pass": True, "shelf": False, "dsr": 0.99, "fwer": 0.01,
               "k_effective": 10, "regime_kill": False, "calmar_overall": 1.3, "notes": {}}
    path = persist_verdict(verdict, str(tmp_path / "run_x"))
    assert (path / "verdict.json").exists()
    assert (path / "SEALED").exists()
    mode = (path / "verdict.json").stat().st_mode
    assert not (mode & stat.S_IWUSR)  # write bit cleared
    loaded = json.loads((path / "verdict.json").read_text())
    assert loaded["dsr"] == 0.99

def test_persist_refuses_overwrite(tmp_path):
    d = tmp_path / "run_y"
    persist_verdict({"pass": True, "shelf": False, "dsr": 1, "fwer": 0, "k_effective": 5,
                     "regime_kill": False, "calmar_overall": 1.0, "notes": {}}, str(d))
    with pytest.raises(FileExistsError):
        persist_verdict({"pass": True, "shelf": False, "dsr": 1, "fwer": 0, "k_effective": 5,
                         "regime_kill": False, "calmar_overall": 1.0, "notes": {}}, str(d))
