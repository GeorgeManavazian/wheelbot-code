from live.data import throttle


class _Resp:
    def __init__(self, code): self.status_code = code


def test_throttle_retries_on_502_then_succeeds():
    calls = {"n": 0}
    def flaky():
        calls["n"] += 1
        return _Resp(502 if calls["n"] < 2 else 200)
    r = throttle(flaky, retries=3)
    assert r.status_code == 200
    assert calls["n"] == 2                        # retried once


def test_throttle_gives_up_after_retries():
    calls = {"n": 0}
    def always_502():
        calls["n"] += 1
        return _Resp(502)
    r = throttle(always_502, retries=2)
    assert r.status_code == 502
    assert calls["n"] == 3                         # initial + 2 retries


def test_throttle_passes_through_200_immediately():
    calls = {"n": 0}
    def ok():
        calls["n"] += 1
        return _Resp(200)
    assert throttle(ok).status_code == 200 and calls["n"] == 1
