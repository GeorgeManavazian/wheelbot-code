import json
import pandas as pd
from live.data import closes_from_json, chain_from_json
from live.market_live import LiveMarket

OBS = pd.Timestamp("2026-07-17")
PH = json.load(open("live/fixtures/price_history_gdx.json"))
OC = json.load(open("live/fixtures/option_chain_gdx_puts.json"))


def _closes_fn(tk):
    # every ticker gets the GDX close history (fine for a mapping test)
    return closes_from_json(PH)


def _chain_fn(tk):
    return chain_from_json(OC, OBS)


def _mkt(held=()):
    return LiveMarket(["GDX", "AAA"], set(held), OBS,
                      closes_fn=_closes_fn, chain_fn=_chain_fn)


def test_spot_is_today_close_or_fallback():
    m = _mkt()
    assert m.spot("GDX", OBS, 0.0) == 71.32          # last fixture close
    assert m.spot("GDX", pd.Timestamp("2099-01-01"), 5.0) == 5.0


def test_regime_row_is_prior_day():
    m = _mkt()
    row = m.regime_row("GDX", OBS)
    assert row is not None and "trend" in row


def test_settle_price_on_or_before_expiry_and_eligible_true():
    m = _mkt()
    # last close on-or-before OBS (the fixture's last date) is today's close 71.32
    assert m.settle_price("GDX", OBS) == 71.32
    # before any close exists -> None
    assert m.settle_price("GDX", pd.Timestamp("2000-01-01")) is None
    assert m.eligible("GDX", OBS) is True


def test_universe_property():
    assert _mkt().universe == ["GDX", "AAA"]


def test_held_ticker_chain_present():
    # a held ticker always gets its chain (needed for management), even if not
    # good-to-rent
    m = _mkt(held=["GDX"])
    assert m.chain("GDX", OBS) is not None
    assert list(m.chain("GDX", OBS).columns)[:3] == ["date", "expiry", "strike"]


def test_pull_failure_skips_ticker():
    def flaky(tk):
        if tk == "BAD":
            raise RuntimeError("BAD price_history -> HTTP 404")
        return closes_from_json(PH)
    m = LiveMarket(["GDX", "BAD"], set(), OBS, closes_fn=flaky, chain_fn=_chain_fn)
    assert "BAD" not in m._closes          # skipped, not fatal
    assert m.spot("GDX", OBS, 0.0) == 71.32


def test_truncated_history_is_recorded_and_logged(capsys):
    """B8: a short-but-valid history (a recently listed name, a truncated
    pull) silently produced regime row None -> permanently ineligible with
    ZERO signal anywhere -- not in skipped_closes, not in any log. It must
    be recorded on the market and logged, WITHOUT joining skipped_closes
    (that would poison the zombie denominators) and while keeping the
    closes stored (a held ticker still needs marks/settlement)."""
    import pandas as pd
    from live.market_live import LiveMarket
    idx = pd.bdate_range("2026-01-01", periods=150)
    short = pd.Series(50.0, index=idx)

    m = LiveMarket(["SHORT"], set(), idx[-1] + pd.Timedelta(days=1),
                   closes_fn=lambda tk: short,
                   chain_fn=lambda tk: (_ for _ in ()).throw(RuntimeError))
    assert m.truncated_closes == [("SHORT", 150)]
    assert "SHORT" not in [t for t, _ in m.skipped_closes]
    assert "SHORT" in m._closes
    assert "truncated" in capsys.readouterr().out.lower()


def test_truncated_covers_the_full_pre_regime_band(capsys):
    """Skeptic F1: the first regime row lands ~273 trading days in (vol
    percentile needs VOL_MIN=252 rank observations), not at WARMUP=200. The
    original len<=200 guard left a 201-273-bar history silently ineligible --
    the exact B8 class, one bar above the check. The discriminator is
    regime_series(s).empty, and a STALE long history (rs non-empty, row None
    by staleness) must NOT be flagged truncated."""
    import pandas as pd
    from live.market_live import LiveMarket

    def mk(n, end="2026-07-20"):
        idx = pd.bdate_range(end=end, periods=n)
        return pd.Series(50.0, index=idx)

    obs = pd.Timestamp("2026-07-21")
    boom = lambda tk: (_ for _ in ()).throw(RuntimeError)

    for n in (201, 260, 272):
        m = LiveMarket(["MID"], set(), obs, closes_fn=lambda tk: mk(n),
                       chain_fn=boom)
        assert m.truncated_closes == [("MID", n)], \
            f"B8/F1: {n}-bar history silently ineligible again"

    m = LiveMarket(["FULL"], set(), obs, closes_fn=lambda tk: mk(300),
                   chain_fn=boom)
    assert m.truncated_closes == [], "a full history must not be flagged"

    stale = mk(400, end="2024-01-05")   # long but years-stale: row None
    m = LiveMarket(["STALE"], set(), obs, closes_fn=lambda tk: stale,
                   chain_fn=boom)
    assert m.truncated_closes == [], \
        "a stale-long history is a different alarm, not truncation"


def test_live_market_never_grows_a_bounded_settle_reach():
    """Skeptic F2: A15's whole live-safety argument is 'LiveMarket does not
    provide bounded_settle_price, so the batch fall-through cannot fire
    live'. Nothing enforced that -- a helpful-looking one-line alias would
    re-enable stale-day settlement live with every test green. Pin it."""
    from live.market_live import LiveMarket
    assert not hasattr(LiveMarket, "bounded_settle_price"), (
        "A15: LiveMarket grew bounded_settle_price -- live settlement would "
        "silently use a stale close; the refuse-and-warn rule (retry when "
        "history is restored) is the LIVE spec. See repair plan A15.")
