"""The governor SPENDING a budget that is about to be destroyed (aegis-yegfx).

Stiwi, 2026-08-04: *"we're about to have our 7 day limit reset. I want to build
in governor support for fully utilizing the limit before it resets."*

WHAT WAS MEASURED THE MOMENT THIS WAS ASKED FOR, because the numbers are the
whole argument: seven_day 86%, refilling in 3h32m, floor P0-only, ZERO ready P0
beads of 213 ready, and the entire fleet down. Fourteen points of a weekly budget
were being conserved by spending none of them, hours before they evaporated. The
tiers were working exactly as specified; the specification was time-blind.

THE LOAD-BEARING TEST IS `test_a_drain_SURVIVES_a_burndown`. Everything else here
is plumbing that can be re-derived by reading the code; that one pins the bound
the whole feature rests on. Burndown is the only mechanism in this module that
makes the fleet spend MORE, so the question a reviewer must be able to answer in
one line is "what stops it?" — and the answer is the drain, which is also what
`burn_ceiling` is computed from. An implementation that passes every other test
here and fails that one has built an off switch for the governor, not a burndown.

THE SECOND THING THIS SUITE PINS is that the fail-safe runs BACKWARDS from the
rest of governor.py. Everywhere else a could-not-tell resolves toward KEEP
RUNNING, because the failure being designed against is a probe bug stopping the
fleet. Here it must resolve toward KEEP THE TIER — a stale reading, an absent
reset timestamp or a reset already past must never open the taps. Four tests
below exist only to hold that direction, because it is the one an editor of this
file would most plausibly "fix" into consistency with its neighbours.

Time is injected and no test sleeps.
"""
from __future__ import annotations

import pytest

from shantytown import config, governor as gov

FIVE, SEVEN = gov.FIVE_HOUR, gov.SEVEN_DAY
T0 = 1_785_600_000.0
HOUR = 3600.0

# The deployed ladder at the time of the bead, both windows, plus a burndown on
# the weekly only. The five-hour window deliberately has NO burndown row: five
# hours is short enough that absolute thresholds behave sanely, and a burndown
# that armed on every five-hour reset would be armed most of the time.
TIERS = """
[governor]
source = "stub"
relax_margin = 5

[[governor.tier]]
at = 50
window = "five_hour"
min_priority = 1

[[governor.tier]]
at = 70
window = "five_hour"
min_priority = 0

[[governor.tier]]
at = 95
window = "five_hour"
action = "drain"

[[governor.tier]]
at = 45
window = "seven_day"
min_priority = 1

[[governor.tier]]
at = 65
window = "seven_day"
min_priority = 0

[[governor.tier]]
at = 75
window = "seven_day"
traits = ["support"]

[[governor.tier]]
at = 90
window = "seven_day"
action = "drain"

[[governor.burndown]]
window = "seven_day"
within = 21600

[roles.support]
attachment = "reports-to"
survival = "support"
lane = ["monitoring"]
"""


class _Clock:
    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> float:
        self.t += seconds
        return self.t


def _policy(tmp_path, text: str = TIERS):
    (tmp_path / "shantytown.toml").write_text(text)
    return config.load(tmp_path).governor


def _evaluate(tmp_path, clock, pct, resets, *, text=TIERS, ok=True, at=None,
              persist=True):
    reader = gov.StubReader(pct=pct, at=clock() if at is None else at, ok=ok,
                            now=clock, resets=resets)
    governor = gov.Governor(_policy(tmp_path, text), reader,
                            gov.FilesGovernorState(tmp_path), now=clock)
    return governor.evaluate(persist=persist)


# The situation the bead was filed from, reused everywhere: weekly nearly spent,
# five-hour budget fresh, weekly reset inside the burndown horizon.
THE_MEASURED_CASE = {FIVE: 3.0, SEVEN: 86.0}


def _resets(clock, seven_in=3.5 * HOUR, five_in=0.85 * HOUR):
    return {FIVE: clock() + five_in, SEVEN: clock() + seven_in}


# --- the load-bearing test ----------------------------------------------------

def test_a_drain_SURVIVES_a_burndown(tmp_path):
    """THE BOUND. Burndown stands down floors and traits; it must never stand
    down the full stop, because the drain is what caps the spending AND is what
    the cap is computed from. Removing it would delete the bound and the thing
    the bound is derived from in one step.

    At 92% the weekly is past its own 90% drain and still inside the burndown
    horizon — so every precondition for a burndown holds EXCEPT the ceiling.
    """
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 3.0, SEVEN: 92.0}, _resets(clock))

    assert v.drains, "the 90% weekly drain must still be in force at 92%"
    assert not v.burning, ("92% is above the 90% ceiling, so the burndown must "
                           "not arm at all — it may never spend past a drain")


def test_a_drain_is_kept_even_while_the_window_IS_burning(tmp_path):
    """The other half of the bound, and the one a naive implementation gets
    wrong: a window can be burning (floors suspended) while a drain from the
    OTHER window still applies. Burndown is per-window and never global.
    """
    clock = _Clock()
    # five_hour at 96 is past its 95% drain; seven_day at 86 is burning.
    v = _evaluate(tmp_path, clock, {FIVE: 96.0, SEVEN: 86.0}, _resets(clock))

    assert [b.window for b in v.burning] == [SEVEN]
    assert v.drains, ("the five_hour drain is untouched by a seven_day "
                      "burndown — the windows are independent budgets")


# --- the mechanism -----------------------------------------------------------

def test_the_measured_case_stands_the_weekly_floor_down(tmp_path):
    """86% weekly, refilling in 3h32m: the floor that produced a fully dammed
    fleet with zero dispatchable beads must be gone."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, THE_MEASURED_CASE, _resets(clock))

    assert v.floor is None, ("the P0-only floor conserves a budget that refills "
                             "in 3.5h — there is nothing left to conserve")
    assert not v.engaged, "no weekly tier should survive, and five_hour is at 3%"
    assert len(v.burning) == 1
    b = v.burning[0]
    assert b.window == SEVEN and b.pct == 86.0
    assert b.ceiling == 90, "the ceiling is the window's own drain threshold"
    assert b.headroom == 4


def test_burning_names_the_tiers_it_stood_down(tmp_path):
    """The observable. A relaxation nobody can see is indistinguishable from a
    governor that stopped working — this is the aegis-yc864 lesson applied to a
    feature that had not shipped yet."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, THE_MEASURED_CASE, _resets(clock))

    stood_down = {t.at for t in v.burning[0].suspended}
    assert stood_down == {45, 65, 75}, "all three non-drain weekly rungs"
    assert not any(t.drains for t in v.burning[0].suspended)

    text = v.burning[0].render()
    assert "BURNDOWN" in text
    assert "seven_day is 86%" in text
    assert "90%" in text and "4 points" in text
    assert "drain is NOT" in text, "the render must state what still bounds it"


def test_only_the_configured_window_stands_down(tmp_path):
    """A five_hour reset is not a licence to spend the weekly. Both budgets are
    near their resets here and only the one with a `burndown` row relaxes."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 72.0, SEVEN: 86.0},
                  _resets(clock, seven_in=1 * HOUR, five_in=0.2 * HOUR))

    assert [b.window for b in v.burning] == [SEVEN]
    assert v.floor == 0, ("the five_hour 70% floor has no burndown row and must "
                          "still apply, even though it also refills shortly")


def test_reserve_lowers_the_ceiling_below_the_drain(tmp_path):
    """For an operator who does not trust the producer's reset clock: raise
    `reserve` rather than turning the feature off."""
    clock = _Clock()
    text = TIERS.replace("within = 21600", "within = 21600\nreserve = 6")

    v = _evaluate(tmp_path, clock, THE_MEASURED_CASE, _resets(clock), text=text)
    assert not v.burning, "86% is above a 90-6 = 84% ceiling"
    assert v.floor == 0, "so the weekly floor still applies"

    v2 = _evaluate(tmp_path, clock, {FIVE: 3.0, SEVEN: 80.0}, _resets(clock),
                   text=text)
    assert v2.burning and v2.burning[0].ceiling == 84


def test_a_window_with_no_drain_is_capped_at_100_minus_reserve(tmp_path):
    """`burn_ceiling` falls back to 100 rather than to 'no cap'. A window with no
    drain declared has no operator-chosen stop, so the only honest ceiling is the
    budget itself."""
    text = TIERS.replace("""[[governor.tier]]
at = 90
window = "seven_day"
action = "drain"

""", "")
    pol = _policy(tmp_path, text)
    burn = pol.burndown_for(SEVEN)
    assert pol.burn_ceiling(SEVEN, burn) == 100


# --- the inverted fail-safe: every could-not-tell KEEPS the tier --------------

def test_a_reset_further_out_than_the_horizon_does_NOT_burn(tmp_path):
    """The ordinary case, and most of the week: 86% with two days to go is a real
    warning and the floor must hold."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, THE_MEASURED_CASE,
                  _resets(clock, seven_in=48 * HOUR))

    assert not v.burning
    assert v.floor == 0, "the P0-only floor is correct with two days left"


def test_no_published_reset_does_NOT_burn(tmp_path):
    """We cannot know the budget is about to be destroyed, so we must assume it
    is not. Note this is the OPPOSITE bias to `Reading.reset_at`'s own docstring,
    where a missing timestamp costs only promptness — there it degrades an ON
    ramp, here it would open the taps."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, THE_MEASURED_CASE, {FIVE: clock() + 3000})

    assert not v.burning
    assert v.floor == 0


def test_a_reset_already_PAST_does_NOT_burn(tmp_path):
    """A reset in the past means the producer has not re-read yet — the reading
    beside it is the OLD budget's. That is the reading least likely to be current
    and so the worst possible one to relax on."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, THE_MEASURED_CASE,
                  _resets(clock, seven_in=-60))

    assert not v.burning
    assert v.floor == 0


def test_a_STALE_reading_does_NOT_burn(tmp_path):
    """The frozen-number failure, pointed at the relaxing mechanism. A stale 86%
    beside a stale reset timestamp would otherwise stand the fleet's floor down
    on a number from last Tuesday."""
    clock = _Clock()
    pol = _policy(tmp_path)
    v = _evaluate(tmp_path, clock, THE_MEASURED_CASE, _resets(clock),
                  at=clock() - pol.max_age_seconds - 60)

    assert v.signal_lost
    assert not v.burning, "a burndown must never be computed from a stale window"


def test_a_FAILED_probe_does_NOT_burn(tmp_path):
    """`probe_success = 0` means the published percentage is the last good one,
    retained and flagged. Burning on it would spend against history."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, THE_MEASURED_CASE, _resets(clock), ok=False)

    assert v.signal_lost
    assert not v.burning


def test_a_burndown_with_nothing_to_stand_down_is_not_reported(tmp_path):
    """At 10% the weekly engages no tier. Recording a burndown here would print
    an alarming line about a fleet that was never restricted — the feature must
    be silent when it changes nothing."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 3.0, SEVEN: 10.0}, _resets(clock))

    assert not v.burning
    assert not v.engaged


# --- burndown suppresses APPLICATION, never the decision ----------------------

def test_the_hysteresis_hold_SURVIVES_a_burndown(tmp_path):
    """Same rule the lost-signal path follows: the hold stays on disk and simply
    is not applied. Otherwise a burndown would silently erase the tier history,
    and the first pass after the reset would read as "never throttled" — losing
    exactly the memory the relax margin exists to provide.
    """
    clock = _Clock()
    # Engage the weekly 75% tier well away from the reset.
    v1 = _evaluate(tmp_path, clock, {FIVE: 3.0, SEVEN: 78.0},
                   _resets(clock, seven_in=48 * HOUR))
    assert {t.at for t in v1.engaged} == {45, 65, 75}

    # Now the reset comes into range: tiers stand down...
    clock.advance(1.0)
    v2 = _evaluate(tmp_path, clock, {FIVE: 3.0, SEVEN: 78.0},
                   _resets(clock, seven_in=2 * HOUR))
    assert v2.burning and not v2.engaged

    # ...and the hold is still recorded, so a pass outside the horizon restores
    # the restriction with no re-climb needed.
    clock.advance(1.0)
    v3 = _evaluate(tmp_path, clock, {FIVE: 3.0, SEVEN: 78.0},
                   _resets(clock, seven_in=48 * HOUR))
    assert {t.at for t in v3.engaged} == {45, 65, 75}, (
        "the burndown must not have cleared the engaged-tier memory")


def test_burndown_is_reported_in_why(tmp_path):
    """A refusal or a status line that cannot state the reading behind it is
    indistinguishable from a bug — the same argument every other message in this
    module makes."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, THE_MEASURED_CASE, _resets(clock))
    assert "BURNDOWN" in v.why
    assert not v.alarm, ("nothing is WRONG — routing this to `alarm` would train "
                         "an operator to ignore the field that means it is")


# --- parsing ------------------------------------------------------------------

def test_the_same_threshold_in_two_windows_now_LOADS(tmp_path):
    """The dedupe was keyed on `at` alone while every tier has been window-
    qualified since aegis-59hao. So the one configuration an operator is most
    likely to want — Stiwi's spoken 50/70/80/95 on BOTH budgets, which the
    deployed config's own comment claims it has — raised "declares `at = 50`
    twice" and refused to load. That is what made aegis-yegfx finding 1's
    recommendation unconfigurable, and the 5-point offset on the weekly ladder
    the only available workaround.
    """
    text = """
[governor]
source = "stub"

[[governor.tier]]
at = 50
window = "five_hour"
min_priority = 1

[[governor.tier]]
at = 50
window = "seven_day"
min_priority = 1
"""
    pol = _policy(tmp_path, text)
    assert {(t.window, t.at) for t in pol.tiers} == {(FIVE, 50), (SEVEN, 50)}


def test_the_same_threshold_TWICE_in_ONE_window_is_still_refused(tmp_path):
    text = """
[governor]
source = "stub"

[[governor.tier]]
at = 50
window = "seven_day"
min_priority = 1

[[governor.tier]]
at = 50
window = "seven_day"
min_priority = 0
"""
    with pytest.raises(Exception, match="twice for window"):
        _policy(tmp_path, text)


def test_burndown_requires_within(tmp_path):
    """No default on purpose: the right horizon is how long this fleet takes to
    actually spend the headroom, which st cannot know."""
    text = TIERS.replace("within = 21600", "")
    with pytest.raises(Exception, match="needs `within`"):
        _policy(tmp_path, text)


def test_burndown_for_an_ungoverned_window_is_refused(tmp_path):
    """A typo in the window name would otherwise fail SILENTLY, and the silence
    reads as "the budget was protected" when the truth is "nothing was ever
    configured"."""
    text = TIERS.replace('window = "seven_day"\nwithin', 'window = "7d"\nwithin')
    with pytest.raises(Exception, match="no \\[\\[governor.tier\\]\\] rows"):
        _policy(tmp_path, text)


def test_two_burndowns_for_one_window_are_refused(tmp_path):
    text = TIERS + """
[[governor.burndown]]
window = "seven_day"
within = 600
"""
    with pytest.raises(Exception, match="twice"):
        _policy(tmp_path, text)


def test_a_reserve_that_can_never_arm_is_refused(tmp_path):
    """100 leaves no headroom at any reading — a silent way to disable the
    feature while the config still claims it is on."""
    text = TIERS.replace("within = 21600", "within = 21600\nreserve = 100")
    with pytest.raises(Exception, match="could never arm"):
        _policy(tmp_path, text)


def test_unknown_burndown_key_is_refused(tmp_path):
    text = TIERS.replace("within = 21600", "within = 21600\nmargin = 3")
    with pytest.raises(Exception, match="unknown key"):
        _policy(tmp_path, text)


def test_no_burndown_row_means_the_ladder_is_unchanged(tmp_path):
    """The default. A deployment that has never heard of this feature must be
    bit-for-bit unaffected, including at a reset it is sitting right on top of."""
    clock = _Clock()
    text = TIERS.replace("""[[governor.burndown]]
window = "seven_day"
within = 21600

""", "")
    v = _evaluate(tmp_path, clock, THE_MEASURED_CASE, _resets(clock), text=text)

    assert not v.burning
    assert v.floor == 0
    assert {t.at for t in v.engaged} == {45, 65, 75}
