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


def test_real_money_defaults_false(tmp_path):
    """A8: paper is the default universe. The flag exists so real-money mode
    has exactly one source of truth -- and today, one refusal gate."""
    cfg = load_run_config(str(tmp_path / "nope.json"))
    assert cfg["real_money"] is False


def test_real_money_must_be_a_strict_bool(tmp_path):
    """1 / "true" / "yes" must not silently mean real money. Only JSON
    true/false parses; anything else aborts the run."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"real_money": True}))
    assert load_run_config(str(p))["real_money"] is True
    for bad in (1, 0, "true", "yes", None):
        p.write_text(json.dumps({"real_money": bad}))
        with pytest.raises(ValueError):
            load_run_config(str(p))


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
