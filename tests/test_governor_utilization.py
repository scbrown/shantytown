"""Per-harness UTILIZATION — the advisory that makes under-cap idleness visible.

The load-bearing properties, in the order they cost something to get wrong:

  * `test_the_measured_gap_recommends_filling` is the case this module exists
    for, with tonight's real numbers.
  * `test_cannot_tell_is_not_hold` — the three-valued answer. An unreadable
    tracker must never render as a full queue.
  * `test_strictest_window_binds` — a tight budget cannot be outvoted by a
    comfortable one.
  * `test_key_is_the_recommendation_not_the_line` — the dedup discipline
    gennaro established in 1641346, which is what keeps a standing advisory from
    paging the coordinator every five minutes with numbers that merely drifted.
"""
from __future__ import annotations

import pytest

from shantytown import governor as gov_mod
from shantytown import governor_utilization as util


NOW = 1_000_000.0
WEEK = gov_mod.WINDOW_LENGTH_S[gov_mod.SEVEN_DAY]
FIVE = gov_mod.WINDOW_LENGTH_S[gov_mod.FIVE_HOUR]


def _reading(pct, *, elapsed, length, ok=True):
    """A reading whose reset timestamp puts the window `elapsed` of the way in."""
    return gov_mod.Reading(pct=pct, at=NOW, ok=ok, source="stub",
                           reset_at=NOW + length * (1.0 - elapsed))


def _policy(*, paces=(), tiers=()):
    return gov_mod.Policy(tiers=tuple(tiers), paces=tuple(paces))


# The real deployed shape: seven_day declares a 1.15x pace bound, five_hour does
# not, and seven_day drains at 90.
_PACES = (gov_mod.Pace(window=gov_mod.SEVEN_DAY, ratio=1.15),)
_TIERS = (gov_mod.Tier(at=90, window=gov_mod.SEVEN_DAY, action="drain"),)


def _assess(**kw):
    kw.setdefault("policy", _policy(paces=_PACES, tiers=_TIERS))
    kw.setdefault("cap", 6)
    kw.setdefault("live", 0)
    kw.setdefault("now", NOW)
    kw.setdefault("ready", 37)
    return util.assess("base", **kw)


def test_the_measured_gap_recommends_filling():
    """THE POINT, with the numbers measured on the live fleet 2026-08-29.

    base: seven_day 52% consumed at 58% elapsed = 0.90x against a 1.15x bound,
    ZERO leads live under a cap of SIX, 37 ready beads. Creel said `hold` and was
    right — the BUDGET is on trajectory. Nothing anywhere said the fleet granted
    six slots was running none of them.
    """
    u = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                     length=WEEK)})
    assert u.advice == 6, u.reason
    assert u.live == 0 and u.cap == 6
    # The reason must be checkable without re-running the tool.
    assert "0.90x" in u.reason and "1.15x" in u.reason and "37 ready" in u.reason
    rendered = u.render()
    assert "live 0/6" in rendered
    assert "↑ fill toward cap: +6" in rendered
    # A ratio is never printed without the numbers it came from.
    assert "52%used/58%elapsed" in rendered


def test_at_cap_holds_and_never_asks_for_ready_work():
    """A full fleet is a proven hold, and must not pay for a tracker query to
    learn it — this command is polled by a status bar every few seconds."""
    u = _assess(live=6, ready=None,
                readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                      length=WEEK)})
    assert u.advice == 0
    assert u.needs_ready is False
    assert "at cap (6/6)" in u.reason


def test_over_its_bound_is_not_under_utilized():
    u = _assess(readings={gov_mod.SEVEN_DAY: _reading(90, elapsed=0.50,
                                                      length=WEEK)})
    assert u.advice == 0
    assert "not under-utilized" in u.reason
    assert "1.80x" in u.reason


def test_strictest_window_binds():
    """seven_day comfortably under pace, five_hour burning hot. The tight budget
    binds — the same direction `Verdict.max_agents` takes for caps."""
    u = _assess(readings={
        gov_mod.FIVE_HOUR: _reading(60, elapsed=0.20, length=FIVE),   # 3.00x
        gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58, length=WEEK),   # 0.90x
    })
    assert u.advice == 0
    assert u.reason.startswith("five_hour is at 3.00x")


def test_cannot_tell_is_not_hold():
    """An unreadable tracker and an empty queue are different facts. Collapsing
    them would let a broken tracker read as a fleet with nothing to do."""
    under = {gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58, length=WEEK)}
    unknown = _assess(readings=under, ready=None)
    empty = _assess(readings=under, ready=0)

    assert unknown.advice is None
    assert unknown.needs_ready is True
    assert "declining to recommend growth on an unproven signal" in unknown.reason
    assert "? cannot tell" in unknown.render()

    assert empty.advice == 0
    assert empty.needs_ready is False
    assert "no ready work" in empty.reason
    assert unknown.key() != empty.key()


def test_a_shrinking_budget_controller_outranks_occupancy():
    """Creel and this module can disagree. When they do, the budget wins — this
    advisory may only ever decline to recommend growth, never authorise it."""
    u = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                      length=WEEK)},
                creel_delta=-2)
    assert u.advice == 0
    assert "budget controller recommends -2" in u.reason


def test_a_growing_budget_controller_does_not_block():
    u = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                      length=WEEK)},
                creel_delta=+2)
    assert u.advice == 6


def test_uncapped_has_nothing_to_fill_toward():
    u = _assess(cap=None,
                readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                       length=WEEK)})
    assert u.advice == 0
    assert "no fleet cap declared" in u.reason
    assert "live 0/uncapped" in u.render()


def test_an_undeclared_bound_says_it_is_linear():
    """five_hour declares no pace. Falling back to 1.00x is arithmetic, not
    policy, and the render must not let it read as a configured threshold."""
    u = _assess(policy=_policy(tiers=_TIERS),
                readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                       length=WEEK)})
    assert "<1.00x linear" in u.render()
    assert u.advice == 6


def test_an_unratable_window_renders_its_reason_not_a_number():
    """No reset timestamp means no pace ratio. `pace_ratio` already refuses with
    a reason; that reason is what belongs on the line."""
    u = _assess(readings={gov_mod.SEVEN_DAY: gov_mod.Reading(
        pct=52, at=NOW, source="stub")})       # no reset_at
    assert u.advice is None
    assert u.needs_ready is False
    assert "no window can be rated" in u.reason
    assert "seven_day unrated" in u.render()
    assert "0.90x" not in u.render()


def test_a_blind_reading_is_not_a_window():
    u = _assess(readings={gov_mod.SEVEN_DAY: gov_mod.Reading(
        pct=None, ok=False, source="stub", error="probe failed")})
    assert u.windows == ()
    assert u.advice is None


def test_the_reason_cites_the_window_with_runway_not_the_deepest_dip():
    """MEASURED 2026-08-29. base was 0.28x on five_hour with 67 points and TWO
    MINUTES to reset, and 0.90x on seven_day with 2d22h left. The deepest
    under-spend was the one no new agent could possibly spend, so citing it would
    have justified a correct recommendation with an uncheckable reason."""
    u = _assess(readings={
        gov_mod.FIVE_HOUR: _reading(28, elapsed=0.99, length=FIVE),   # 0.28x
        gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58, length=WEEK),   # 0.90x
    })
    assert u.advice == 6
    assert u.reason.startswith("seven_day at 0.90x")
    # both windows still REPORTED — the choice is about the justification only
    assert "five_hour 28%used/99%elapsed" in u.render()


def test_points_stop_at_the_drain_not_at_100():
    """The fleet cannot spend past its own drain, so points beyond it are not
    headroom. Derived from the tiers, so it cannot drift from the drain."""
    u = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                      length=WEEK)})
    seven = [w for w in u.windows if w.window == gov_mod.SEVEN_DAY][0]
    assert seven.ceiling == 90.0
    assert seven.points == pytest.approx(38.0)


def test_key_is_the_recommendation_not_the_line():
    """gennaro's 1641346 discipline. Elapsed climbs and points fall on every
    pass, so a line-keyed ledger would re-page the coordinator every five minutes
    with a recommendation they have already read."""
    early = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                          length=WEEK)})
    later = _assess(readings={gov_mod.SEVEN_DAY: _reading(53, elapsed=0.60,
                                                          length=WEEK)})
    assert early.render() != later.render()      # the numbers moved
    assert early.key() == later.key()            # the recommendation did not

    # ...but a changed recommendation is a changed key.
    filled = _assess(live=3,
                     readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                           length=WEEK)})
    assert filled.advice == 3
    assert filled.key() != early.key()


def test_assess_is_pure():
    """The 45vco replay discipline: a total function of its inputs. Same inputs,
    same answer, no clock and no I/O — which is what lets the caller run it once
    to decide whether a tracker query is even worth paying for."""
    kw = dict(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                    length=WEEK)})
    assert _assess(**kw) == _assess(**kw)
