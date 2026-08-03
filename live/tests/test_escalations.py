"""A10b/C16b: conditions that log daily but never email must escalate after
ESCALATION_DAYS consecutive stepped days. Unit tests for the counter ledger +
wiring pins on run_daily (source pins, C13 precedent -- main() needs the full
harness; the predicate itself is behaviorally tested here)."""
import json
import re

from live.escalations import update_escalations, ESCALATION_DAYS


def _p(tmp_path):
    return str(tmp_path / "escalations.json")


def test_streak_escalates_at_threshold_not_before(tmp_path):
    p = _p(tmp_path)
    assert update_escalations("2026-08-03", {"unquoted_leg": ["XYZ_081526P30"]}, p) == {}
    assert update_escalations("2026-08-04", {"unquoted_leg": ["XYZ_081526P30"]}, p) == {}
    out = update_escalations("2026-08-05", {"unquoted_leg": ["XYZ_081526P30"]}, p)
    assert out == {"unquoted_leg": [("XYZ_081526P30", 3)]}
    # persists and keeps escalating daily while present (FROZEN-alert shape)
    out = update_escalations("2026-08-06", {"unquoted_leg": ["XYZ_081526P30"]}, p)
    assert out == {"unquoted_leg": [("XYZ_081526P30", 4)]}


def test_absent_day_resets_the_streak(tmp_path):
    p = _p(tmp_path)
    update_escalations("2026-08-03", {"call_gated_unclosable": ["SLV"]}, p)
    update_escalations("2026-08-04", {"call_gated_unclosable": ["SLV"]}, p)
    # surface ran, condition gone -> reset
    assert update_escalations("2026-08-05", {"call_gated_unclosable": []}, p) == {}
    update_escalations("2026-08-06", {"call_gated_unclosable": ["SLV"]}, p)
    update_escalations("2026-08-07", {"call_gated_unclosable": ["SLV"]}, p)
    out = update_escalations("2026-08-10", {"call_gated_unclosable": ["SLV"]}, p)
    assert out == {"call_gated_unclosable": [("SLV", 3)]}, \
        "streak must rebuild from 1 after a clean day, and stepped-day gaps must not reset"


def test_same_day_recall_is_idempotent_and_never_resets(tmp_path):
    """A partial-failure retry re-runs the surfaces the same day: a key seen
    by the first call and absent from the second must keep its streak, and a
    double-sighting must not double-count."""
    p = _p(tmp_path)
    update_escalations("2026-08-03", {"unquoted_leg": ["A", "B"]}, p)
    update_escalations("2026-08-03", {"unquoted_leg": ["A"]}, p)   # B absent, same day
    st = json.load(open(p))
    assert st["unquoted_leg"]["A"]["count"] == 1
    assert st["unquoted_leg"]["B"]["count"] == 1, \
        "same-day re-call must not reset a survivor"


def test_kind_not_passed_is_left_untouched(tmp_path):
    p = _p(tmp_path)
    update_escalations("2026-08-03", {"unquoted_leg": ["A"]}, p)
    update_escalations("2026-08-04", {"call_gated_unclosable": ["SLV"]}, p)
    st = json.load(open(p))
    assert st["unquoted_leg"]["A"]["count"] == 1, \
        "a kind absent from `seen` must be untouched, not reset"


def test_two_kinds_report_independently(tmp_path):
    p = _p(tmp_path)
    for day in ("2026-08-03", "2026-08-04", "2026-08-05"):
        out = update_escalations(day, {"unquoted_leg": ["A"],
                                       "call_gated_unclosable": ["SLV"]}, p)
    assert out == {"unquoted_leg": [("A", 3)],
                   "call_gated_unclosable": [("SLV", 3)]}


def test_run_daily_wiring_pins():
    """Source pins (C13 precedent -- a main() harness costs more than it
    catches here; the WHEELBOT_STATE_DIR import trap applies): run_daily must
    (1) collect call_gated_unclosable across accounts, (2) feed BOTH kinds to
    update_escalations gated on NOT smoke, (3) alert on a non-empty result."""
    src = open("live/run_daily.py").read()
    assert re.search(r'gated_all \|= \{str\(w\[2\]\) for w in r\.warnings'
                     r'[\s\S]{0,80}call_gated_unclosable', src), \
        "C16b: call_gated_unclosable is not collected across accounts"
    # A21 re-pin: both kinds still wired, still smoke-gated -- PLUS the new
    # error gate (a day the held-quote surface did not run must OMIT
    # unquoted_leg, never pass a false "clean" that resets a dead symbol's
    # streak).
    assert re.search(r'if not args\.smoke:[\s\S]{0,700}'
                     r'seen = \{"call_gated_unclosable": sorted\(gated_all\)\}'
                     r'[\s\S]{0,500}if merged\["error"\] is None:'
                     r'[\s\S]{0,100}seen\["unquoted_leg"\] = merged\["unquoted"\]'
                     r'[\s\S]{0,100}update_escalations\(day, seen\)',
                     src), \
        "A10b/C16b: update_escalations not wired (or not smoke-gated, or " \
        "the A21 failed-surface omit rule is gone)"
    assert re.search(r'update_escalations\([\s\S]{0,1500}ESCALATION_DAYS', src) and \
        re.search(r'if esc:[\s\S]{0,1500}_alert\(', src), \
        "A10b/C16b: escalation result does not reach _alert"
