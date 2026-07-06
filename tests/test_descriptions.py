"""Every concrete strategy must explain itself in plain language (spec:
'Strategy descriptions' — mandatory, not optional polish)."""
from src.strategies.base import Strategy
from src.strategies.momentum_rotation import MomentumRotation  # noqa: F401
from src.strategies.ts_trend import TSTrend  # noqa: F401

REQUIRED_PARTS = ("What it does", "Why it should work", "When it fails")


def test_every_concrete_strategy_has_full_description():
    concrete = Strategy.__subclasses__()
    assert concrete, "no strategies discovered"
    for cls in concrete:
        desc = getattr(cls, "description", "")
        assert len(desc) > 100, f"{cls.name}: description missing or too thin"
        for part in REQUIRED_PARTS:
            assert part in desc, f"{cls.name}: description lacks '{part}' section"
