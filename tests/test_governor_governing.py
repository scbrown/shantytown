"""The display must name the restriction the enforcement is applying (aegis-yc864).

THE LOAD-BEARING TEST IS `test_the_displayed_tier_and_the_enforced_floor_AGREE`.
Everything else here is a specimen; that one is the invariant, and it is the only
thing that stays true when someone adds a third budget.

The bug this pins was not a wrong number, it was TWO ANSWERS TO ONE QUESTION.
`Verdict.floor` took the strictest floor across every engaged tier — correct — and
`st crew --governor` / `st tend` rendered `engaged[-1]`, a POSITIONAL pick carrying
the comment "cumulative, so the last one is the most restrictive". That was true
while every tier read one budget. The two-budget change (aegis-59hao) made
`engaged` span WINDOWS, and across windows position stopped implying strictness —
but the caller kept its justification, which is why nothing caught it.

Measured live: five_hour engaged at 50 (min_priority=1), seven_day at 65
(min_priority=0). `st go` refused P1. The status line said "dispatch only P1 and
above [five_hour >= 50%]".

WHY THAT PARTICULAR DISAGREEMENT IS EXPENSIVE, and not just untidy: the dispatch
refusal offers "raise its priority" as the way out. So an operator who trusts the
display sees a floor of P1, sees a P1 refused, and is invited to edit REAL WORK to
clear a floor that was never where the display said. That is a priority-inflation
pump driven by an instrument disagreeing with itself, and it caught the person who
had just written the warning about it.
"""
from __future__ import annotations

import pytest

from shantytown.governor import DRAIN, FIVE_HOUR, SEVEN_DAY, Reading, Tier, Verdict


def _verdict(*tiers: Tier) -> Verdict:
    return Verdict(reading=Reading(), engaged=tuple(tiers))


# The live shape that produced the bug.
FIVE_50 = Tier(at=50, min_priority=1, window=FIVE_HOUR)
SEVEN_65 = Tier(at=65, min_priority=0, window=SEVEN_DAY)
FIVE_90_DRAIN = Tier(at=90, action=DRAIN, window=FIVE_HOUR)


# --- the invariant --------------------------------------------------------------


@pytest.mark.parametrize("tiers", [
    (FIVE_50, SEVEN_65),
    (SEVEN_65, FIVE_50),                      # order must not matter — that IS the bug
    (FIVE_50,),
    (SEVEN_65,),
    (FIVE_50, SEVEN_65, Tier(at=80, window=FIVE_HOUR, traits=("support",))),
])
def test_the_displayed_tier_and_the_enforced_floor_AGREE(tiers):
    """THE test. Whatever `admits` enforces is what a human is shown.

    Stated as an invariant over tier sets rather than as one expected string,
    because the failure was never "wrong text" — it was two computations over the
    same list that nobody had ever asserted were equal.
    """
    v = _verdict(*tiers)
    assert v.floor is not None
    assert v.governing is not None
    assert v.governing.min_priority == v.floor, (
        f"display names P{v.governing.min_priority} "
        f"[{v.governing.window} >= {v.governing.at}%] "
        f"while enforcement refuses anything above P{v.floor}"
    )


def test_ORDER_of_engaged_does_not_change_the_answer():
    """The positional pick's whole failure mode, isolated."""
    assert _verdict(FIVE_50, SEVEN_65).governing is _verdict(SEVEN_65, FIVE_50).governing


# --- the specimen, and the proof the old code would have failed it ---------------


def test_the_live_two_budget_case_names_the_seven_day_tier():
    v = _verdict(FIVE_50, SEVEN_65)
    assert v.governing is SEVEN_65
    assert v.governing.label() == "dispatch only P0 and above [seven_day >= 65%]"


def test_the_old_positional_pick_would_have_been_WRONG_here():
    """Non-vacuity: without this, the tests above could pass against `engaged[-1]`.

    A regression test that the buggy implementation also passes is not a
    regression test.
    """
    v = _verdict(FIVE_50, SEVEN_65)
    assert v.engaged[-1] is SEVEN_65          # this ordering happens to be right...
    v2 = _verdict(SEVEN_65, FIVE_50)          # ...and this one is the shipped failure
    assert v2.engaged[-1] is FIVE_50
    assert v2.engaged[-1] is not v2.governing
    assert v2.engaged[-1].label() != v2.governing.label()


# --- a drain outranks any floor -------------------------------------------------


def test_a_drain_outranks_a_floor_even_though_it_declares_none():
    """A drain refuses EVERYTHING, including P0, so it is the strictest thing
    engaged — but it carries `min_priority=None`, so a naive "strictest floor"
    would skip it and announce a floor while the fleet is being told to stop."""
    v = _verdict(FIVE_50, SEVEN_65, FIVE_90_DRAIN)
    assert v.governing is FIVE_90_DRAIN
    assert "FULL STOP" in v.governing.label()


def test_no_engaged_tiers_is_None_not_a_crash():
    """`st crew --governor` prints an empty label rather than inventing a
    restriction — the ungoverned case is a real state, not an error."""
    assert _verdict().governing is None
