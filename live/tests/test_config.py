import json
import pytest
from live.config import load_run_config, DEFAULTS


def test_missing_file_returns_defaults(tmp_path):
    cfg = load_run_config(str(tmp_path / "nope.json"))
    assert cfg == DEFAULTS


def test_overrides_from_file(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"n": 10, "capital": 25000}))
    cfg = load_run_config(str(p))
    assert cfg["n"] == 10
    assert cfg["capital"] == 25000.0
    assert isinstance(cfg["capital"], float)


def test_partial_file_keeps_other_default(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"n": 3}))
    cfg = load_run_config(str(p))
    assert cfg["n"] == 3
    assert cfg["capital"] == DEFAULTS["capital"]


def test_bad_values_raise(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"n": 0}))
    with pytest.raises(ValueError):
        load_run_config(str(p))
    p.write_text(json.dumps({"capital": -5}))
    with pytest.raises(ValueError):
        load_run_config(str(p))
    p.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ValueError):
        load_run_config(str(p))
