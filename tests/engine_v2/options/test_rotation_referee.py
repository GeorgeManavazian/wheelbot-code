import os

import pytest

from scripts.audit_defense_execution import audit_rotation
from src.engine_v2.options.data import chain_path

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.path.exists(chain_path("SPY")),
                       reason="chain data not pulled (gitignored) -- runs locally, skipped in CI"),
]


def test_rotation_referee_clean_n1():
    assert audit_rotation(n_slots=1) == 0


def test_rotation_referee_clean_n5():
    assert audit_rotation(n_slots=5) == 0
