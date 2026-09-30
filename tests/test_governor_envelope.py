"""The seven_day pace bound as a SPENDING ENVELOPE (aegis-zowv5j).

Stiwi 2026-09-27: "I want the big up front burst like we did, but we need to
conserve some budget for the final days of the limit."

A constant ratio cannot say that, and it fails in BOTH directions:

  * early week, a real burst — 20% used at 5% elapsed = 4x — reads as overspend
    against 1.5x and is HELD, which blocks the burst;
  * late week, 1.5x at 66% elapsed permits 99% used, so NOTHING is reserved for
    the final days. The live reading that prompted this: 86% used at 66% elapsed
    = 1.31x < 1.50x -> "+8 fill toward cap".

The envelope declares, per elapsed point, the most the window may have USED.
The arms below are the success criteria written on the bead BEFORE the build,
run through the real evaluate() and the real utilization recommender — not a
re-derivation of the arithmetic.
"""
from __future__ import annotations

import pytest

from shantytown import config, governor as gov
from shantytown import governor_utilization as gu

FIVE, SEVEN = gov.FIVE_HOUR, gov.SEVEN_DAY
T0 = 1_785_600_000.0
HOUR = 3600.0
WEEK = 7 * 24 * HOUR

# sattler's initial curve, verbatim from the bead.
CURVE = "[[0, 20], [14, 40], [43, 65], [71, 80], [86, 90], [100, 100]]"

LADDER = """
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
at = 30
window = "seven_day"
min_priority = 1

[[governor.tier]]
at = 70
window = "seven_day"
min_priority = 0

[[governor.tier]]
at = 95
window = "seven_day"
action = "drain"
"""

ENVELOPE = LADDER + f"""
[[governor.pace]]
window = "seven_day"
curve = {CURVE}
"""

CONSTANT = LADDER + """
[[governor.pace]]
window = "seven_day"
ratio = 1.5
"""


def _policy(tmp_path, text):
    (tmp_path / "shantytown.toml").write_text(text)
    return config.load(tmp_path).governor


def _resets(elapsed: float) -> dict:
    return {FIVE: T0 + 0.5 * HOUR, SEVEN: T0 + WEEK * (1.0 - elapsed)}


def _evaluate(tmp_path, text, used, elapsed, resets=None):
    clock = lambda: T0  # noqa: E731 — time is injected, nothing sleeps
    reader = gov.StubReader(pct={FIVE: 3.0, SEVEN: used}, at=T0, ok=True,
                            now=clock,
                            resets=_resets(elapsed) if resets is None else resets)
    governor = gov.Governor(_policy(tmp_path, text), reader,
                            gov.FilesGovernorState(tmp_path), now=clock)
    return governor.evaluate(persist=False)


def _util(tmp_path, text, used, elapsed):
    policy = _policy(tmp_path, text)
    readings = {SEVEN: gov.Reading(pct=used, at=T0,
                                   reset_at=T0 + WEEK * (1.0 - elapsed))}
    return gu.assess("base", readings=readings, policy=policy, cap=9, live=1,
                     now=T0, ready=20)


# --- the envelope itself ------------------------------------------------------

def test_the_envelope_interpolates_the_declared_points(tmp_path):
    pace = _policy(tmp_path, ENVELOPE).pace_for(SEVEN)
    assert pace.envelope(0.0) == pytest.approx(20.0)
    assert pace.envelope(0.14) == pytest.approx(40.0)
    assert pace.envelope(0.66) == pytest.approx(77.3, abs=0.1), \
        "the bead's worked number: the envelope at 66% elapsed is ~77%"
    assert pace.envelope(1.0) == pytest.approx(100.0)
    # A bound is the envelope in ratio form: far above today's 1.5x early, and
    # well under it late. It never drops below 1.0x — the curve ends at 100 —
    # so the "reserve" is budget NOT burned by the burst, not a sub-linear week.
    assert pace.bound(0.05) > 4.0
    assert pace.bound(0.66) == pytest.approx(1.17, abs=0.01)
    assert pace.bound(0.9) == pytest.approx(1.03, abs=0.01)


# --- the bead's success criteria, through the REAL gate and recommender -------

# THE BEAD'S FIRST ARM WAS (10%, 35%) GROWS, AND ITS OWN CURVE SAYS OTHERWISE:
# the envelope at 10% elapsed is 20 + 20 x 10/14 = 34.3%, so 35% is 0.7 points
# outside it. Both rows are pinned so the boundary is visible, not smoothed over;
# which one Stiwi meant is a curve edit, not a code change (reported on the bead).
@pytest.mark.parametrize("elapsed,used,grows", [
    (0.10, 33.0, True),    # the front-loaded burst: 3.3x pace, inside envelope
    (0.10, 35.0, False),   # the bead's arm: 0.7 points OUTSIDE its own curve
    (0.66, 86.0, False),   # the live reading that said "+8" — must now HOLD
    (0.90, 88.0, True),    # the reserve is spendable in the final day
])
def test_the_recommender_follows_the_envelope(tmp_path, elapsed, used, grows):
    u = _util(tmp_path, ENVELOPE, used, elapsed)
    assert (u.advice > 0) is grows, u.render()
    if not grows:
        assert u.cause == "over-pace", u.render()
        assert "envelope" in u.reason and f"{used:.0f}% used" in u.reason


@pytest.mark.parametrize("elapsed,used,paced", [
    (0.10, 33.0, True),    # inside the envelope -> tiers stand down
    (0.10, 35.0, False),   # just outside it (see the note above)
    (0.66, 86.0, False),   # outside it -> the ladder stands
    (0.90, 88.0, True),
])
def test_the_gate_follows_the_envelope(tmp_path, elapsed, used, paced):
    v = _evaluate(tmp_path, ENVELOPE, used, elapsed)
    assert bool(v.pacing) is paced
    if paced:
        assert not v.engaged, "an admitted burn withholds every non-drain tier"
        assert v.pacing[0].envelope_pct is not None
    else:
        assert v.engaged, "outside the envelope the ladder stands as configured"


def test_the_constant_ratio_would_have_got_the_live_reading_WRONG(tmp_path):
    """The control: the same (66%, 86%) reading under today's 1.5x grows. If
    this ever fails, the envelope arm above proves nothing about the change."""
    assert _util(tmp_path, CONSTANT, 86.0, 0.66).advice > 0
    assert _util(tmp_path, CONSTANT, 35.0, 0.10).advice == 0, \
        "and it held the burst the envelope admits"


def test_an_undefined_elapsed_leaves_the_gate_INERT(tmp_path):
    """No reset timestamp -> no elapsed -> the tiers stand exactly as configured.
    The fail-safe direction pace has always had; the envelope must not invert it
    by treating 'unknown elapsed' as 'envelope at 0% = 20% allowed'."""
    v = _evaluate(tmp_path, ENVELOPE, 25.0, 0.0, resets={FIVE: T0 + HOUR})
    assert not v.pacing
    assert v.floor is None, "control: 25% engages nothing, so nothing to withhold"
    v = _evaluate(tmp_path, ENVELOPE, 35.0, 0.0, resets={FIVE: T0 + HOUR})
    assert not v.pacing, "35% would be inside ANY envelope reading — still inert"
    assert v.floor == 1, "the 30 rung stands exactly as configured"


def test_a_drain_still_survives_the_envelope(tmp_path):
    """96% at 99% elapsed is inside the envelope (99.4%) — and the drain holds."""
    v = _evaluate(tmp_path, ENVELOPE, 96.0, 0.99)
    assert v.pacing, "inside the envelope, so the soft rungs stand down"
    assert v.drains, "but the 95 drain is never a pace question"


def test_a_fresh_window_is_rated_against_the_envelope_at_zero(tmp_path):
    """At 0% elapsed the ratio form divides by zero; the envelope does not."""
    u = _util(tmp_path, ENVELOPE, 0.0, 0.0)
    seven = [w for w in u.windows if w.window == SEVEN][0]
    assert seven.under is True and seven.envelope_pct == pytest.approx(20.0)
    assert "20% envelope" in seven.render()


def test_the_live_line_states_the_envelope_WITH_its_numbers(tmp_path):
    u = _util(tmp_path, ENVELOPE, 86.0, 0.66)
    line = u.render()
    assert "86%used/66%elapsed" in line and "vs 77% envelope" in line, line


# --- regression: a constant-ratio row is unchanged ----------------------------

@pytest.mark.parametrize("elapsed,used", [
    (0.10, 35.0), (0.66, 86.0), (0.90, 88.0), (0.50, 60.0), (0.675, 65.0)])
def test_a_constant_ratio_row_behaves_exactly_as_before(tmp_path, elapsed, used):
    pace = _policy(tmp_path, CONSTANT).pace_for(SEVEN)
    assert pace.curve == () and pace.bound(elapsed) == 1.5
    ratio, _ = gov.pace_ratio(used, T0 + WEEK * (1 - elapsed), T0, WEEK)
    assert pace.admits(used, ratio, elapsed) is (ratio <= 1.5)
    v = _evaluate(tmp_path, CONSTANT, used, elapsed)
    assert bool(v.pacing) is (ratio <= 1.5 and used >= 30)


# --- config validation --------------------------------------------------------

@pytest.mark.parametrize("curve,needle", [
    ("[[0, 20]]", "at least two"),
    ("[[5, 20], [100, 100]]", "start at elapsed 0"),
    ("[[0, 20], [90, 100]]", "end at elapsed 100"),
    ("[[0, 20], [50, 10], [100, 100]]", "never decrease"),
    ("[[0, 20], [50, 50], [50, 60], [100, 100]]", "strictly increase"),
    ("[[0, 20], [50, 150], [100, 100]]", "outside 0..100"),
    ("[[0, true], [100, 100]]", "numbers"),
])
def test_a_malformed_curve_is_REFUSED(tmp_path, curve, needle):
    text = ENVELOPE.replace(CURVE, curve)
    with pytest.raises(config.ConfigError) as e:
        _policy(tmp_path, text)
    assert needle in str(e.value)


def test_ratio_AND_curve_together_is_refused(tmp_path):
    text = ENVELOPE + "ratio = 1.5\n"
    with pytest.raises(config.ConfigError) as e:
        _policy(tmp_path, text)
    assert "both" in str(e.value)


def test_an_envelope_survives_the_portable_policy_wire(tmp_path):
    """A peer host receives the policy over the wire; the curve must arrive as
    the same envelope, and a constant row must not arrive with an empty one."""
    from shantytown import fleet_governor as fg
    for text in (ENVELOPE, CONSTANT):
        policy = _policy(tmp_path, text)
        back = gov.parse(fg.policy_wire(policy))
        assert back.pace_for(SEVEN) == policy.pace_for(SEVEN)
