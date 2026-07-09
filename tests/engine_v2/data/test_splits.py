import pathlib
import pytest
import pandas as pd
from src.engine_v2.data.loader import load_bars
from src.engine_v2.data.splits import (
    PLAYGROUND, EXAM, slice_playground, slice_exam,
    checksum_split, seal_splits, ExamAccessDenied,
)

BARS = load_bars(["SPY", "TLT", "GLD"], "2007-01-01", "2010-12-31")

def test_playground_slice_bounds():
    pg = slice_playground(BARS)
    assert pg.index.min() >= PLAYGROUND[0]
    assert pg.index.max() <= PLAYGROUND[1]

def test_exam_requires_allow_flag():
    with pytest.raises(ExamAccessDenied):
        slice_exam(BARS, allow=False, run_id="test")

def test_exam_access_logged(tmp_path):
    log = tmp_path / "exam.log"
    slice_exam(BARS, allow=True, run_id="unit-abc", log_path=str(log))
    lines = log.read_text().strip().splitlines()
    assert len(lines) == 1
    assert "unit-abc" in lines[0]

def test_checksum_deterministic():
    pg = slice_playground(BARS)
    assert checksum_split(pg) == checksum_split(pg)

def test_seal_writes_once(tmp_path):
    d = tmp_path / "splits"
    info = seal_splits(BARS, sealed_dir=str(d))
    assert (d / "SEALED.txt").exists()
    assert "playground_sha256" in info
    with pytest.raises(FileExistsError):
        seal_splits(BARS, sealed_dir=str(d))
