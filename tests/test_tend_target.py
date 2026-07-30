"""`st tend --target N` — respawn TOWARD a count (aegis-9brfz, the st touchpoint).

The elastic-scaling thread of the st-redesign epic has exactly one mechanism half:
respawn toward a number. Everything else about scaling — WHICH agents a fleet should
consist of, when to consolidate roles — is judgment, and the epic's load-bearing
split puts judgment in the administrator and mechanism in st. So the properties
pinned here are as much about what this does NOT do.
"""
from __future__ import annotations

from shantytown import tend as tend_mod
from shantytown.protocols import Agent
from shantytown.tmux import NullPanes


def _tender(live, target=None, spawned=None):
    panes = NullPanes(live=set(live))

    class _RT:
        def shows_ready_ui(self, s):
            return True

    return tend_mod.Tender(
        panes, _RT(), None,
        spawn=lambda c, p: (spawned if spawned is not None else []).append(c.name),
        refresh=None, ensure=lambda card: card.workspace,
        now=lambda: 1000.0, log=lambda m: None, target=target)


def _fleet():
    return [Agent(name="admin", role="administrator", pane="p-admin"),
            Agent(name="dee", role="lead", pane="p-dee", reports_to="admin"),
            Agent(name="bond", role="worker", pane="p-bond", reports_to="dee"),
            Agent(name="felix", role="worker", pane="p-felix", reports_to="dee")]


def _verdicts(rep):
    return {f.agent: f.verdict for f in rep.findings}


def test_no_target_respawns_the_WHOLE_roster():
    """The compatibility floor: a pass without --target is what it always was."""
    spawned = []
    rep = _tender(live=set(), spawned=spawned).pass_over(_fleet())
    assert sorted(spawned) == ["admin", "bond", "dee", "felix"]
    assert set(_verdicts(rep).values()) == {tend_mod.RESPAWNED}


def test_a_target_caps_how_many_come_up():
    spawned = []
    rep = _tender(live=set(), target=2, spawned=spawned).pass_over(_fleet())
    assert len(spawned) == 2
    held = [a for a, v in _verdicts(rep).items() if v == tend_mod.BELOW_TARGET]
    assert len(held) == 2


def test_the_tier_fills_from_the_ROOT_DOWN():
    """A fleet brought up bottom-first is workers whose stop events reach nobody."""
    spawned = []
    _tender(live=set(), target=2, spawned=spawned).pass_over(_fleet())
    assert spawned == ["admin", "dee"], "administrator, then lead — deterministic"


def test_ALREADY_LIVE_agents_count_toward_the_target():
    """It respawns toward a COUNT, not by a count. Otherwise every pass adds N more
    and a 5-minute timer walks the fleet up forever."""
    spawned = []
    rep = _tender(live={"p-admin", "p-dee"}, target=2, spawned=spawned).pass_over(_fleet())
    assert spawned == [], "the target is already met"
    assert _verdicts(rep)["bond"] == tend_mod.BELOW_TARGET
    assert _verdicts(rep)["felix"] == tend_mod.BELOW_TARGET


def test_a_SURPLUS_is_never_stopped():
    """The line between mechanism and judgment. Scale-UP only — a tend that could
    also kill would be a scheduler holding a supervisor's permissions."""
    spawned = []
    rep = _tender(live={"p-admin", "p-dee", "p-bond", "p-felix"}, target=1,
                  spawned=spawned).pass_over(_fleet())
    assert spawned == []
    assert not any(f.acted for f in rep.findings), "it stopped nobody"


def test_a_held_agent_is_NOT_a_fault():
    """Being under the operator's own cap is not a defect, so the pass stays
    healthy and the exit code does not turn red."""
    rep = _tender(live=set(), target=1).pass_over(_fleet())
    assert rep.healthy()
    assert rep.faults == []


def test_a_held_agent_is_still_REPORTED_with_the_number():
    """A down agent nothing mentions is indistinguishable from one the supervisor
    failed to notice."""
    rep = _tender(live=set(), target=1).pass_over(_fleet())
    held = next(f for f in rep.findings if f.verdict == tend_mod.BELOW_TARGET)
    assert "--target 1" in held.why
    assert "not a fault" in held.why and "raise the target" in held.why


def test_a_RETIRED_card_is_never_brought_up_to_meet_a_target():
    """Retirement outranks the count. Filling a target by reviving something the
    operator durably shut down is exactly the watchdog bug retirement exists for."""
    fleet = _fleet() + [Agent(name="ghost", role="worker", pane="p-ghost",
                              retired=True)]
    spawned = []
    rep = _tender(live=set(), target=9, spawned=spawned).pass_over(fleet)
    assert "ghost" not in spawned
    assert _verdicts(rep)["ghost"] == tend_mod.RETIRED


def test_a_retired_card_does_not_consume_target_headroom():
    """It is not part of the fleet being counted, in either direction."""
    fleet = [Agent(name="admin", role="administrator", pane="p-admin"),
             Agent(name="ghost", role="worker", pane="p-ghost", retired=True),
             Agent(name="bond", role="worker", pane="p-bond")]
    spawned = []
    _tender(live=set(), target=2, spawned=spawned).pass_over(fleet)
    assert sorted(spawned) == ["admin", "bond"], "both, not one-plus-a-retiree"


def test_a_target_of_zero_brings_up_NOTHING():
    """0 is a real answer and must not read as 'unset'. The operator standing the
    fleet down is the case this is most likely to be used for."""
    spawned = []
    _tender(live=set(), target=0, spawned=spawned).pass_over(_fleet())
    assert spawned == []


def test_a_card_with_NO_PANE_is_untendable_either_way():
    rep = _tender(live=set(), target=4).pass_over(
        [Agent(name="nym", role="worker", pane=None)])
    assert rep.findings[0].verdict == tend_mod.UNTENDABLE


def test_a_DRY_RUN_under_a_target_still_shows_who_would_come_up():
    spawned = []
    rep = _tender(live=set(), target=2, spawned=spawned).pass_over(
        _fleet(), dry_run=True)
    assert spawned == [], "a dry run launches nothing"
    would = [f.agent for f in rep.findings if f.verdict == tend_mod.WOULD]
    assert would == ["admin", "dee"], "and names exactly the two the cap allows"


def test_an_unknown_role_sorts_LAST_not_first():
    """A deployment-declared role (traits.py) is not in the built-in reporting
    process, so nothing in it depends on that agent being up."""
    fleet = [Agent(name="stiwi", role="advisor", pane="p-stiwi"),
             Agent(name="admin", role="administrator", pane="p-admin")]
    spawned = []
    _tender(live=set(), target=1, spawned=spawned).pass_over(fleet)
    assert spawned == ["admin"]
