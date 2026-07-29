import json
import pytest
from live.run_daily import zombie_check
from live.config import load_run_config


def _zc(skipped_closes=0, universe_size=547, chain_attempts=30, chains_ok=30,
        threshold=0.5):
    return zombie_check(skipped_closes, universe_size, chain_attempts,
                        chains_ok, threshold)


def test_zombie_check_boundaries():
    # a handful of delisted/halted names is normal
    assert _zc(skipped_closes=3) is False
    assert _zc(skipped_closes=200) is False
    # exactly at threshold -> zombie (>= is the rule)
    assert _zc(skipped_closes=274) is True
    # the 2026-07-24 case: everything failed
    assert _zc(skipped_closes=547) is True


def test_zombie_check_empty_universe_is_zombie():
    """An empty universe means the pull produced nothing at all -- never a
    'quiet but healthy' day."""
    assert _zc(universe_size=0, chain_attempts=0, chains_ok=0) is True


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
