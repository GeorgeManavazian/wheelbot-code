import json
import pytest
from live.run_daily import zombie_check
from live.config import load_run_config


def test_zombie_check_boundaries():
    # a handful of delisted/halted names is normal
    assert zombie_check(skipped=3, universe_size=547, threshold=0.5) is False
    assert zombie_check(skipped=200, universe_size=547, threshold=0.5) is False
    # exactly at threshold -> zombie (>= is the rule)
    assert zombie_check(skipped=274, universe_size=547, threshold=0.5) is True
    # the 2026-07-24 case: everything failed
    assert zombie_check(skipped=547, universe_size=547, threshold=0.5) is True


def test_zombie_check_empty_universe_is_zombie():
    """An empty universe means the pull produced nothing at all -- never a
    'quiet but healthy' day."""
    assert zombie_check(skipped=0, universe_size=0, threshold=0.5) is True


def test_config_exposes_zombie_threshold_default(tmp_path):
    cfg = load_run_config(str(tmp_path / "absent.json"))
    assert cfg["zombie_threshold"] == 0.5


def test_config_zombie_threshold_override(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"n": 5, "capital": 100000, "zombie_threshold": 0.8}))
    assert load_run_config(str(p))["zombie_threshold"] == 0.8


def test_config_rejects_out_of_range_threshold(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"zombie_threshold": 1.5}))
    with pytest.raises(ValueError):
        load_run_config(str(p))


def test_config_rejects_zero_threshold(tmp_path):
    """0 would make every run a zombie."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"zombie_threshold": 0}))
    with pytest.raises(ValueError):
        load_run_config(str(p))
