"""The governor judging a weekly budget on BURN RATE, not on the calendar
(aegis-7kwtu).

THE DEFECT, WITH THE NUMBERS THAT PROVE IT IS NOT A TUNING ACCIDENT. A tier
compares consumption to a constant with no reference to how much of the window
has ELAPSED, so two readings that mean opposite things are treated identically:

    65% consumed at hour 20 of 168   -> 5.5x pace, a real emergency
    65% consumed at hour 113 of 168  -> 0.96x pace, slightly UNDER linear

Measured 2026-08-02: week 67.5% elapsed, budget 65.0% consumed, pace 0.96x — and
the ENTIRE FLEET was stopped, 0 ready P0s of 213 ready beads, for ~54h. The ladder
is most aggressive precisely when it should be most relaxed, and any fixed
threshold is crossed by an on-pace burn at a predictable hour, every week, forever.

THE LOAD-BEARING TESTS ARE TWO, and they pull in opposite directions:

  * `test_a_drain_SURVIVES_pace` — the bound. Pace withholds floors and traits and
    must never withhold the full stop, because a drain is about TOTAL SPEND and
    total spend is not a pace question. 96% consumed is the last of the budget
    however elegantly it was burned. An implementation passing everything else and
    failing this one has built an off switch for the governor.

  * `test_an_undefined_ratio_leaves_the_tiers_ALONE` and its siblings — the
    fail-safe DIRECTION, which the bead named as the likeliest bug in the feature.
    Burndown RELAXES, so its could-not-tells resolve toward KEEPING the tier. Pace
    is a GATE ON a relaxation, so its could-not-tells must resolve toward the gate
    being INERT — the tiers stay exactly as configured. Read literally, "resolve
    toward not engaging" would stand the tiers DOWN on a missing reset timestamp,
    i.e. a computation nobody could do would REMOVE a spend guard. These tests
    exist to stop an editor "fixing" the direction into that reading.

Time is injected and no test sleeps.
"""
from __future__ import annotations

import pytest

from shantytown import config, governor as gov

FIVE, SEVEN = gov.FIVE_HOUR, gov.SEVEN_DAY
T0 = 1_785_600_000.0
HOUR = 3600.0
WEEK = 7 * 24 * HOUR

# Stiwi's spoken ladder (50/70/80/95), which this feature deliberately does NOT
# reopen — plus a pace row on the weekly only. The five-hour window has no pace
# row on purpose: five hours is short enough that absolute thresholds behave
# sanely, and a fresh five-hour window is 0% consumed at 0% elapsed, which is an
# undefined ratio at exactly the moment it would be least useful.
TIERS = """
[governor]
source = "stub"
relax_margin = 5

[[governor.tier]]
at = 50
window = "five_hour"
min_priority = 1

[[governor.tier]]
at = 95
window = "five_hour"
action = "drain"

[[governor.tier]]
at = 50
window = "seven_day"
min_priority = 1

[[governor.tier]]
at = 70
window = "seven_day"
min_priority = 0

[[governor.tier]]
at = 80
window = "seven_day"
traits = ["support"]

[[governor.tier]]
at = 95
window = "seven_day"
action = "drain"

[[governor.pace]]
window = "seven_day"
ratio = 1.15

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


def _at_elapsed(clock, fraction: float) -> dict:
    """Resets placing the weekly window `fraction` of the way through."""
    return {FIVE: clock() + 0.5 * HOUR,
            SEVEN: clock() + WEEK * (1.0 - fraction)}


# --- the incident this bead was filed from ------------------------------------

def test_THE_2026_08_02_INCIDENT_no_longer_stops_the_fleet(tmp_path):
    """65% consumed at 67.5% elapsed = 0.96x. The fleet must run.

    This is the measured case, verbatim: a P0-only floor engaged with zero ready
    P0 beads board-wide and nothing was dispatchable for ~54h, on a burn that was
    UNDER linear. Under the fixed ladder 65% clears the 50% floor; under pace it
    is simply a normal week.
    """
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 3.0, SEVEN: 65.0},
                  _at_elapsed(clock, 0.675))

    assert v.pacing, "0.96x pace must withhold the weekly tiers"
    p = v.pacing[0]
    assert p.window == SEVEN
    assert p.ratio == pytest.approx(0.963, abs=0.01)
    assert not v.engaged, "no restriction may survive an on-pace weekly burn"
    assert v.floor is None, ("the P0-only floor is the thing that stopped the "
                             "fleet for 54h and must be gone")


def test_the_SAME_reading_early_in_the_week_still_throttles(tmp_path):
    """65% at hour 20 of 168 = 5.5x pace. Identical number, opposite meaning.

    This is the control that proves pace DISCRIMINATES rather than just disabling
    the weekly ladder. If this test and the one above both pass, the mechanism is
    reading elapsed time; if only the one above passes, the ladder is simply off.
    """
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 3.0, SEVEN: 65.0},
                  _at_elapsed(clock, 20.0 / 168.0))

    assert not v.pacing, "5.5x pace is overspending — the tiers must stand"
    assert v.engaged, "the weekly ladder must engage on a genuine overspend"
    assert v.floor == 1, ("65% clears the 50% tier (min_priority = 1) and not "
                          "the 70% one, so the floor is P1 — the ladder is "
                          "engaged, at the rung the reading actually justifies")


# --- the bound ----------------------------------------------------------------

def test_a_drain_SURVIVES_pace(tmp_path):
    """THE BOUND. A drain is about TOTAL SPEND, which is not a pace question.

    96% consumed at 99% elapsed is 0.97x — impeccably on pace, and also the last
    of the budget. "But we were on pace" is not a reason to spend the remainder.
    """
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 3.0, SEVEN: 96.0},
                  _at_elapsed(clock, 0.99))

    assert v.drains, "the 95% weekly drain must survive an on-pace burn"
    assert any(t.drains for t in v.engaged)
    if v.pacing:
        assert not any(t.drains for t in v.pacing[0].suspended), (
            "pace may withhold floors and traits, never the full stop")


# --- the fail-safe direction (the bead's predicted bug) -----------------------

def test_an_undefined_ratio_leaves_the_tiers_ALONE(tmp_path):
    """No reset timestamp -> no ratio -> the gate is INERT, not open.

    THE DIRECTION IS THE POINT. A missing input must never be the reason a spend
    guard comes off: the fleet would run unthrottled on an exhausted weekly budget
    because nobody could do a division.
    """
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 3.0, SEVEN: 85.0}, {})

    assert not v.pacing, "an uncomputable ratio must not stand anything down"
    assert v.engaged, "the configured ladder must still be in force"
    assert v.floor == 0, "85% is above the 70% P0-only floor and stays there"


def test_a_reset_already_past_leaves_the_tiers_ALONE(tmp_path):
    """A reset in the past is a producer that has not caught up, not a fresh
    budget. Deriving elapsed >= 1 from it would compute a fully-elapsed window
    from the stalest number available — and a fully-elapsed window makes almost
    any burn look on-pace, which is the most dangerous possible misread."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 3.0, SEVEN: 85.0},
                  {FIVE: clock() + 0.5 * HOUR, SEVEN: clock() - HOUR})

    assert not v.pacing
    assert v.engaged, "a stale reset must not open the taps"


def test_a_window_length_too_short_to_be_right_is_REFUSED(tmp_path):
    """The self-check that makes inferring `seven_day -> 168h` safe.

    A window cannot have more time remaining than it has in total, so
    `reset_at - now > length` PROVES the length is wrong. The ratio must go
    undefined rather than be computed on a premise already known to be false.
    Configured here as a 1-hour weekly window with a reset 3 hours out.
    """
    text = TIERS.replace('ratio = 1.15', 'ratio = 1.15\nlength = 3600')
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 3.0, SEVEN: 85.0},
                  {FIVE: clock() + 0.5 * HOUR, SEVEN: clock() + 3 * HOUR},
                  text=text)

    assert not v.pacing, "a provably-wrong window length must not produce a ratio"
    assert v.engaged


def test_a_stale_reading_never_reaches_pace(tmp_path):
    """Same rule burndown follows: only readings we ACCEPTED may stand a tier
    down. A percentage too old to govern by is too old to relax by."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 3.0, SEVEN: 65.0},
                  _at_elapsed(clock, 0.675),
                  at=clock() - 4000.0)  # older than max_age

    assert not v.pacing, "a stale window must never be judged on pace"


# --- the ratio itself ---------------------------------------------------------

@pytest.mark.parametrize("consumed,elapsed,expected", [
    (65.0, 0.675, 0.963),   # the incident
    (65.0, 20 / 168, 5.46),  # same reading, hour 20
    (90.0, 0.982, 0.916),   # live 2026-08-04: 90% with 3h left of the week
    (50.0, 0.50, 1.0),      # dead linear
])
def test_pace_ratio_arithmetic(consumed, elapsed, expected):
    now = T0
    reset_at = now + WEEK * (1.0 - elapsed)
    ratio, why = gov.pace_ratio(consumed, reset_at, now, int(WEEK))
    assert why == ""
    assert ratio == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("reset_at,length", [
    (None, int(WEEK)),          # nothing published
    (T0 - 1.0, int(WEEK)),      # already past
    (T0 + WEEK, None),          # unknown window length
    (T0 + 2 * WEEK, int(WEEK)),  # length provably too short
])
def test_pace_ratio_says_WHY_it_cannot_answer(reset_at, length):
    """Every undefined case returns a reason, never a bare None. A gate that
    cannot say why it stood aside is indistinguishable from one that is broken."""
    ratio, why = gov.pace_ratio(85.0, reset_at, T0, length)
    assert ratio is None
    assert why, "an undefined ratio must carry its reason"


# --- the reset boundary (aegis-lvfm5) -----------------------------------------
#
# For exactly one tick per window, `left` sits at or a hair past `length` and the
# strict guard read that as PROOF of a bad configuration — measured saying "reset
# is 7d 0h away but the window is only 7d 0h long" about a correct 7-day window.
# The lane then reported `unrated`, and since the governor is the only brake, the
# brake read BLIND for that tick. A blind gauge must never be read as green, so
# refusing to answer was, uniquely here, the less honest option: a window with
# nothing spent and the whole budget ahead is not unrateable, it is WIDE OPEN.

@pytest.mark.parametrize("overshoot", [0.0, 1.0, 30.0, gov.RESET_BOUNDARY_SKEW_S])
def test_a_JUST_RESET_window_rates_wide_open_and_is_never_unrated(overshoot):
    """0 used with the full budget ahead is 0.00x, not a refusal to answer."""
    ratio, why = gov.pace_ratio(0.0, T0 + WEEK + overshoot, T0, int(WEEK))
    assert why == "", f"a fresh reset must not be unrated (overshoot {overshoot}s)"
    assert ratio == pytest.approx(0.0), "nothing spent is 0.00x — wide open"


def test_the_length_guard_STILL_refuses_a_genuinely_short_window():
    """The skew allowance must not weaken the guard it sits in front of. A length
    that is really wrong is wrong by a large fraction of the window, never by a
    minute — so one minute of tolerance costs the guard nothing, and this test is
    what says so."""
    ratio, why = gov.pace_ratio(0.0, T0 + WEEK, T0, int(WEEK // 2))
    assert ratio is None
    assert "too short to be right" in why

    just_past = gov.pace_ratio(0.0, T0 + WEEK + gov.RESET_BOUNDARY_SKEW_S + 1.0,
                               T0, int(WEEK))
    assert just_past[0] is None, "beyond the allowance is still refused"
    assert "too short to be right" in just_past[1]


def test_usage_in_a_window_that_has_not_started_stays_UNRATED():
    """The other side of the boundary case, and the reason it is not simply
    `elapsed <= 0 -> 0.0`. Spend reported against a window that has not begun is
    not a pace of zero, it is a reading to distrust — and distrust is spelled
    `unrated`, loudly, exactly as before."""
    ratio, why = gov.pace_ratio(5.0, T0 + WEEK, T0, int(WEEK))
    assert ratio is None, "usage before the window starts must not rate as open"
    assert "has not started" in why and "5%" in why


def test_the_TICK_LINE_reads_wide_open_at_the_boundary_not_unrated():
    """The bead's own close criterion, at the level it was reported from: the
    line an operator reads on `st tend`. `0.00x` is the answer; the word
    `unrated` on a lane that is genuinely wide open is the bug."""
    from shantytown import governor_utilization as gu

    class _R:
        ok = True
        pct = 0.0
        reset_at = T0 + WEEK          # the exact boundary

    line = gu._window_use(gov.Policy(), SEVEN, _R(), T0).render()
    assert "unrated" not in line, f"the brake still reads blind: {line}"
    assert "0.00x" in line, line
    assert "0%used" in line and "0%elapsed" in line, line


def test_elapsed_is_never_reported_NEGATIVE_at_the_boundary():
    """The display half. `pace_ratio` pins elapsed at 0; the utilization line
    derives its own percentage from the same inputs, so it is clamped too or an
    operator reads a window running backwards."""
    from shantytown import governor_utilization as gu

    class _R:
        ok = True
        pct = 0.0
        reset_at = T0 + WEEK + 30.0

    use = gu._window_use(gov.Policy(), SEVEN, _R(), T0)
    assert use is not None
    assert use.ratio == pytest.approx(0.0), "and it is rated, not None"
    assert use.elapsed_pct is not None and use.elapsed_pct >= 0.0


# --- observability (design constraint 5) --------------------------------------

def test_the_pace_line_states_BOTH_numbers(tmp_path):
    """A pace line reporting only the ratio is uncheckable. An operator must be
    able to see the consumed and elapsed fractions it was computed from."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 3.0, SEVEN: 65.0},
                  _at_elapsed(clock, 0.675))

    text = v.pacing[0].render()
    assert "65% consumed" in text
    assert "68% elapsed" in text or "67% elapsed" in text
    assert "0.96x" in text
    assert "drain is NOT waived" in text


# --- composition with burndown (design constraint 2) --------------------------

def test_pace_and_burndown_COMPOSE(tmp_path):
    """Both withhold the same non-drain tiers; a window in both is reported by
    both. They are two independent reasons and an operator should see each."""
    text = TIERS + """
[[governor.burndown]]
window = "seven_day"
within = 21600
"""
    clock = _Clock()
    # 88% consumed at 99% elapsed = 0.89x pace, AND resetting in ~1.7h.
    v = _evaluate(tmp_path, clock, {FIVE: 3.0, SEVEN: 88.0},
                  _at_elapsed(clock, 0.99), text=text)

    assert v.pacing and v.burning, "both mechanisms should recognise this window"
    assert not v.engaged, "and the result is the same tiers standing down"


# --- config validation --------------------------------------------------------

def test_a_pace_row_for_a_window_with_no_tiers_is_REFUSED(tmp_path):
    text = TIERS.replace('[[governor.pace]]\nwindow = "seven_day"',
                         '[[governor.pace]]\nwindow = "typo_day"')
    with pytest.raises(Exception) as e:
        _policy(tmp_path, text)
    assert "typo_day" in str(e.value)


def test_an_unknown_window_is_refused_BEFORE_pace_ever_sees_it(tmp_path):
    """MEASURED, and it corrects what this test first asserted.

    The parse-time "no knowable length" guard in `_pace` is real but is NOT
    reachable through config today: a pace row must name a window that has tiers,
    and the TIER validator already refuses any window the producer does not
    publish. So the length guard is unreachable belt-and-braces, and a test
    claiming it fires here would be pinning the wrong error message — it would
    pass for a reason unrelated to pace and go on passing if the guard were
    deleted. Pinned as the layering it actually is, with the pace guard covered
    directly by the unit test below instead.
    """
    text = TIERS + """
[[governor.tier]]
at = 50
window = "monthly"
min_priority = 1

[[governor.pace]]
window = "monthly"
ratio = 1.2
"""
    with pytest.raises(Exception) as e:
        _policy(tmp_path, text)
    assert "monthly" in str(e.value)


def test_a_pace_row_with_no_knowable_length_cannot_compute(tmp_path):
    """The guard the config path cannot currently reach, exercised directly.

    If the producer ever adds a third window, this is what stops a pace row on it
    from sitting inert forever while LOOKING configured.
    """
    assert gov.Pace(window="monthly", ratio=1.2).window_length() is None
    ratio, why = gov.pace_ratio(85.0, T0 + WEEK, T0,
                                gov.Pace(window="monthly",
                                         ratio=1.2).window_length())
    assert ratio is None and "length" in why


def test_ratio_is_required(tmp_path):
    text = TIERS.replace("ratio = 1.15", "")
    with pytest.raises(Exception) as e:
        _policy(tmp_path, text)
    assert "ratio" in str(e.value)
