from scripts.audit_defense_execution import audit_rotation


def test_rotation_referee_clean_n1():
    assert audit_rotation(n_slots=1) == 0


def test_rotation_referee_clean_n5():
    assert audit_rotation(n_slots=5) == 0
