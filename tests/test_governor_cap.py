"""The governor's FLEET-SIZE CAP (aegis-3vt4h).

Stiwi, 2026-08-04: *"have the governor add a cap, we clearly need a mechanism."*

WHAT WENT WRONG, MEASURED. Immediately after a weekly budget rollover the
coordinator relaunched the full 16-agent roster, because nothing forbade it and
Rule Zero says never leave capacity idle. Sixteen agents burned ~37%/hr of the
five_hour budget against ~10%/hr sustainable: the 50% floor engaged after ~80
minutes, most of the fleet went idle, the window cleared, and it engaged again.
A sawtooth that reads as a busy fleet and is capacity burning budget to no
purpose. The coordinator then wrote two analyses of the resulting idle as a
governor design gap, when the cause was the roster size chosen an hour earlier.

THE LOAD-BEARING TEST IS `test_the_baseline_cap_SURVIVES_a_lost_signal`. Every
other restriction in this module is derived from a reading and is therefore
correctly dropped when the probe dies — that is the fail-safe, and it is right,
because a probe bug must never STOP a crew. The cap is the one input that is NOT
a reading: it is a number an operator wrote down. If it were dropped with the
tiers, a dead probe would UNCAP the fleet, and the failure above would recur
precisely when nobody can see the budget. An implementation that passes
everything else and fails that one has built a cap that evaporates exactly when
it is most needed.

THE OTHER THING THIS SUITE PINS is that a cap NEVER KILLS. `tend` is
scale-up-on-loss and nothing else; a cap withholds a respawn, it does not stop a
running agent. `max_agents = 0` is refused at parse time for the same reason —
an operator who wants a fleet stopped has `action = "drain"`, which carries the
push protocol; a cap of 0 would stop it with none of those guarantees.

Time is injected and no test sleeps.
"""
from __future__ import annotations

import pytest

from shantytown import config, governor as gov

FIVE, SEVEN = gov.FIVE_HOUR, gov.SEVEN_DAY
T0 = 1_785_600_000.0

BASE = """
[governor]
source = "stub"
max_agents = 6

[[governor.tier]]
at = 50
window = "five_hour"
min_priority = 1

[[governor.tier]]
at = 80
window = "five_hour"
max_agents = 2

[[governor.tier]]
at = 95
window = "five_hour"
action = "drain"
"""


class _Clock:
    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _policy(tmp_path, text: str = BASE):
    (tmp_path / "shantytown.toml").write_text(text)
    return config.load(tmp_path).governor


def _verdict(tmp_path, pct, *, text=BASE, ok=True, at=None, error=""):
    clock = _Clock()
    reader = gov.StubReader(pct=pct, at=clock() if at is None else at, ok=ok,
                            error=error, now=clock)
    return gov.Governor(_policy(tmp_path, text), reader,
                        gov.FilesGovernorState(tmp_path), now=clock).evaluate()


# --- the load-bearing test ----------------------------------------------------

def test_the_baseline_cap_SURVIVES_a_lost_signal(tmp_path):
    """THE POINT OF A BASELINE. Every tier is dropped when blind — correctly,
    since a remembered tier must not be applied to an unmeasured present. The cap
    is not a reading, so blindness is no reason to relax it. A dead probe must not
    be able to uncap a fleet."""
    v = _verdict(tmp_path, 10.0, ok=False)

    assert v.signal_lost
    assert not v.engaged, "tiers are correctly dropped while blind"
    assert v.max_agents == 6, ("the operator's declared cap needs no probe and "
                               "must outlive one")


def test_a_cap_NEVER_kills_it_only_withholds(tmp_path):
    """`tend` is scale-up-on-loss. A cap is an input to how many come UP; nothing
    here may stop a live agent, which is the supervisor-vs-scheduler split the
    whole module rests on. Pinned by the absence of any drain/stop implication."""
    v = _verdict(tmp_path, 10.0)

    assert v.max_agents == 6
    assert not v.drains, "a cap is not a drain and must never imply one"
    assert v.floor is None, "and it is not a priority floor either"


# --- composition --------------------------------------------------------------

def test_no_cap_configured_means_uncapped(tmp_path):
    """The default. A deployment that has never heard of this key is unaffected."""
    text = BASE.replace("max_agents = 6\n", "")
    v = _verdict(tmp_path, 10.0, text=text)
    assert v.max_agents is None


def test_a_tier_cap_TIGHTENS_the_baseline(tmp_path):
    """At 80% the tier declares 2 against a baseline of 6. Strictest wins, exactly
    as min_priority composes."""
    v = _verdict(tmp_path, 85.0)
    assert v.max_agents == 2


def test_a_tier_cap_never_LOOSENS_the_baseline(tmp_path):
    """The direction that matters. A tier declaring a LARGER number than the
    baseline must not raise it — a cap can only ever shrink."""
    text = BASE.replace("max_agents = 2", "max_agents = 99")
    v = _verdict(tmp_path, 85.0, text=text)
    assert v.max_agents == 6, ("a tier asking for 99 under a baseline of 6 gets "
                               "6 — min(), never max()")


def test_a_tier_without_a_cap_inherits_the_one_below(tmp_path):
    """At 50% only the floor tier is engaged and it declares no cap, so the
    baseline stands — a tier that is silent about size does not erase the size."""
    v = _verdict(tmp_path, 55.0)
    assert v.floor == 1
    assert v.max_agents == 6


def test_the_cap_applies_at_ZERO_usage(tmp_path):
    """The case the feature exists for. Every tier is silent at 0%; the failure
    being prevented happens at 0%, right after a budget reset."""
    v = _verdict(tmp_path, 0.0)
    assert not v.engaged
    assert v.max_agents == 6, "a fresh budget is not a licence to launch 16 agents"


# --- the enforcement seam -----------------------------------------------------

def test_effective_target_takes_the_stricter_of_ask_and_cap():
    from shantytown.cli import _effective_target as eff
    assert eff(None, None) is None      # neither declared -> whole roster
    assert eff(20, None) == 20          # operator only
    assert eff(None, 6) == 6            # governor only
    assert eff(20, 6) == 6              # a request cannot exceed a constraint
    assert eff(3, 6) == 3               # ...and a stricter request still wins


# --- parsing ------------------------------------------------------------------

def test_max_agents_zero_is_REFUSED(tmp_path):
    """A silent full stop with none of the drain protocol's guarantees. An
    operator who means that has `action = "drain"`."""
    text = BASE.replace("max_agents = 6", "max_agents = 0")
    with pytest.raises(Exception, match="drain"):
        _policy(tmp_path, text)


def test_negative_and_non_integer_caps_are_refused(tmp_path):
    for bad in ("-1", "1.5", '"six"', "true"):
        text = BASE.replace("max_agents = 6", f"max_agents = {bad}")
        with pytest.raises(Exception, match="max_agents"):
            _policy(tmp_path, text)


def test_a_tier_cap_parses_and_reaches_the_tier(tmp_path):
    pol = _policy(tmp_path)
    assert pol.max_agents == 6
    t80 = pol.tier_at(80, FIVE)
    assert t80 is not None and t80.max_agents == 2


def test_the_tier_label_names_the_cap(tmp_path):
    """A restriction that cannot state itself is indistinguishable from a bug."""
    pol = _policy(tmp_path)
    assert "at most 2 agents live" in pol.tier_at(80, FIVE).label()


def test_the_held_message_names_the_GOVERNOR_not_a_flag_nobody_passed():
    """A report that names the wrong SOURCE is the aegis-yc864 shape one layer
    down: the number is right and the explanation is not. "--target 6 is already
    met" sent an operator hunting for a flag they never passed, when the 6 came
    from the governor's cap.

    Ties go to the governor: a cap the operator happens to match is still the
    thing that would stop them raising it.
    """
    from shantytown.cli import _target_source as src
    assert src(None, None) is None          # no cap, no ask -> tend's own wording
    assert src(20, None) is None            # operator only -> "--target"
    assert "max_agents" in (src(None, 6) or "")   # cap only -> the governor
    assert "max_agents" in (src(20, 6) or "")     # cap BINDS -> the governor
    assert src(3, 6) is None, "operator asked stricter — --target is honest"
    assert "max_agents" in (src(6, 6) or ""), "tie goes to the governor"
