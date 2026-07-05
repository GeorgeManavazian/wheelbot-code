import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from src.engine import data


def make_long(dates, tickers):
    rows = []
    for d in dates:
        for t in tickers:
            rows.append({"date": d, "ticker": t, "open": 10.0, "high": 10.0,
                         "low": 10.0, "close": 10.0, "volume": 100})
    return pd.DataFrame(rows)


@pytest.fixture
def frozen(tmp_path):
    dates = pd.bdate_range("2020-01-01", periods=4)
    df = make_long(dates, ["A", "B"])
    for split in ("playground", "exam"):
        (tmp_path / split).mkdir()
        df.to_parquet(tmp_path / split / f"{split}.parquet")
    manifest = {}
    for split in ("playground", "exam"):
        p = tmp_path / split / f"{split}.parquet"
        manifest[split] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                           "rows": len(df)}
    (tmp_path / "manifests").mkdir()
    (tmp_path / "manifests" / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


def test_load_playground_verifies_checksum(frozen):
    df = data.load_playground(data_dir=frozen)
    assert len(df) == 8


def test_load_playground_rejects_tampered_file(frozen):
    p = frozen / "playground" / "playground.parquet"
    p.write_bytes(p.read_bytes() + b"tamper")
    with pytest.raises(RuntimeError, match="checksum"):
        data.load_playground(data_dir=frozen)


def test_load_exam_refuses_without_confirm(frozen):
    with pytest.raises(RuntimeError, match="exam"):
        data.load_exam(data_dir=frozen)


def test_load_exam_logs_when_confirmed(frozen):
    data.load_exam(confirm=True, data_dir=frozen)
    log = (frozen / "manifests" / "exam_runs.log").read_text()
    assert len(log.strip().splitlines()) == 1


def test_to_wide_closes(frozen):
    wide = data.to_wide_closes(data.load_playground(data_dir=frozen))
    assert list(wide.columns) == ["A", "B"]
    assert len(wide) == 4
    assert wide.index.is_monotonic_increasing
