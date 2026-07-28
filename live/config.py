"""Run-config for the daily paper step. Edit N (concurrent slots) and starting
capital in ONE place -- data/live/config.json -- instead of the launchd wrapper.

Precedence: CLI flag > config.json > built-in DEFAULTS. A missing config.json
is fine (DEFAULTS apply). NOTE: capital only takes effect on a FRESH state --
a running account keeps its saved cash, so reset state.json to change it."""
from __future__ import annotations
import json

CONFIG_PATH = "data/live/config.json"
DEFAULTS = {"n": 5, "capital": 100_000.0, "zombie_threshold": 0.5}


def load_run_config(path: str = CONFIG_PATH) -> dict:
    """Return {"n": int, "capital": float}, DEFAULTS overlaid by any keys in the
    JSON object at `path`. Missing file -> DEFAULTS. Malformed content or an
    out-of-range value raises (better to abort than trade a garbage size)."""
    cfg = dict(DEFAULTS)
    try:
        with open(path) as f:
            raw = json.load(f)
    except FileNotFoundError:
        return cfg
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a JSON object, got {type(raw).__name__}")
    if "n" in raw:
        cfg["n"] = int(raw["n"])
    if "capital" in raw:
        cfg["capital"] = float(raw["capital"])
    if "zombie_threshold" in raw:
        cfg["zombie_threshold"] = float(raw["zombie_threshold"])
    if cfg["n"] < 1:
        raise ValueError(f"{path}: n must be >= 1, got {cfg['n']}")
    if cfg["capital"] <= 0:
        raise ValueError(f"{path}: capital must be > 0, got {cfg['capital']}")
    if not 0.0 < cfg["zombie_threshold"] <= 1.0:
        raise ValueError(f"{path}: zombie_threshold must be in (0, 1], "
                         f"got {cfg['zombie_threshold']}")
    return cfg
