"""The floor exemption — `exempt` in [governor] (aegis-yegfx).

WHY THE FEATURE EXISTS, measured 2026-08-02: seven_day usage at 70% engaged a
P0-only floor while the board held ZERO P0 beads. Every agent went idle for ~54h
with 19 P1s ready. The floor did exactly what it was configured to do and the
result was a total stall — the remaining budget was being conserved by spending
none of it.

WHAT THESE TESTS ARE REALLY GUARDING is the narrowness. An exemption is a hole in
a spend guard, and the ways it could go wrong are all "it applies somewhere it
should not": to a drain, to a freeze, to an agent nobody named, or silently. Most
of the file is therefore negative — the admit path is two tests and the refusals
are the rest. `test_a_waiver_is_never_silent` is the one that matters most: a
bypass nobody can see is indistinguishable from a governor that is switched off,
which is the exact state the module's fail-safe section refuses to allow.
"""
from __future__ import annotations

import pytest

from shantytown import config, governor as gov
from shantytown.dispatch import Dispatcher, GovernorRefused
from shantytown.protocols import WorkItem

from tests.test_governor import (SPOKEN, _Panes, _Registry, _Tracker, ORDINARY,
                                 _gov, _item)


# The spoken tiers, plus ellie named as floor-exempt. Written as TOML rather than
# as a Policy object for the reason the main suite gives: "changed in
# shantytown.toml with NO code edit" is itself the deliverable.
EXEMPT = SPOKEN.replace(
    '[governor]\nsource = "stub"\nrelax_margin = 5',
    '[governor]\nsource = "stub"\nrelax_margin = 5\nexempt = ["ellie"]')


def _v(tmp_path, pct):
    return _gov(tmp_path, pct, text=EXEMPT).evaluate()


# --- the admit path (deliberately short) --------------------------------------

def test_an_exempt_agent_clears_a_floor_that_refuses_everyone_else(tmp_path):
    """The whole feature, at the tier that caused it: P0-only, zero P0s ready."""
    v = _v(tmp_path, 75)
    assert v.floor == 0, "precondition: the P0-only floor is in force"
    assert v.admits(_item("st-1", priority=1), "ellie") == ""
    assert v.admits(_item("st-1", priority=2), "ellie") == ""
    refusal = v.admits(_item("st-1", priority=1), "tim")
    assert refusal, "the floor must still bind every agent nobody named"
    assert "P1" in refusal


def test_an_exempt_agent_also_clears_the_no_priority_refusal(tmp_path):
    """An unprioritised item is refused under a floor because its importance was
    never stated. That reasoning is ABOUT THE FLOOR, so the exemption carries it
    too — otherwise an exempt agent hits a refusal whose stated rationale (the
    floor) does not apply to them, which is the confusing half-state."""
    assert _v(tmp_path, 75).admits(_item("st-1", priority=None), "ellie") == ""
    assert _v(tmp_path, 75).admits(_item("st-1", priority=None), "tim")


# --- the narrowness: everywhere it must NOT apply ------------------------------

def test_a_drain_is_not_waivable(tmp_path):
    """FULL STOP means full stop. A drain has asked every agent to push and stop;
    handing an exempt one new work contradicts the instruction it is already
    acting on. Spend, not priority — a different question from the floor."""
    v = _v(tmp_path, 97)
    assert v.drains, "precondition: the drain tier is engaged"
    refusal = v.admits(_item("st-1", priority=0), "ellie")
    assert refusal and "FULL STOP" in refusal
    assert not v.waives(_item("st-1", priority=0), "ellie")


def test_a_freeze_is_not_waivable(tmp_path):
    """on_signal_lost = freeze means we cannot SEE the budget. An exemption is a
    judgement that one agent's lane was misjudged by a fleet-wide floor; it is
    not a claim to know a number nobody can read."""
    text = EXEMPT.replace('source = "stub"',
                          'source = "stub"\non_signal_lost = "freeze"')
    v = _gov(tmp_path, 75, text=text, ok=False).evaluate()
    assert v.frozen, "precondition: the signal is lost and the policy freezes"
    refusal = v.admits(_item("st-1", priority=2), "ellie")
    assert refusal and "FROZEN" in refusal
    assert not v.waives(_item("st-1", priority=2), "ellie")


def test_an_unnamed_agent_is_not_exempt_and_neither_is_nobody(tmp_path):
    v = _v(tmp_path, 75)
    assert v.admits(_item("st-1", priority=2), "tim")
    assert v.admits(_item("st-1", priority=2), None), (
        "agent=None is the unassigned-work path and must stay governed")
    assert v.admits(_item("st-1", priority=2))


def test_the_exemption_changes_nothing_when_no_floor_is_engaged(tmp_path):
    """Below every threshold the governor is invisible, and it stays invisible
    for an exempt agent too — there is no floor to waive, so nothing is waived
    and nothing is announced."""
    v = _gov(tmp_path, 45, text=EXEMPT, state=False).evaluate()
    assert v.admits(_item("st-1", priority=4), "ellie") == ""
    assert not v.waives(_item("st-1", priority=4), "ellie")


# --- the announcement ---------------------------------------------------------

def test_a_waiver_is_never_silent(tmp_path):
    """`waives` is the REPORTING predicate and it must be true exactly when the
    exemption changed the outcome."""
    v = _v(tmp_path, 75)
    assert v.waives(_item("st-1", priority=2), "ellie")
    said = v.waiver_says(_item("st-1", priority=2), "ellie")
    assert "ellie" in said and "st-1" in said and "P2" in said
    assert "exempt" in said and "[governor]" in said, (
        "a waiver that does not say where it is configured cannot be undone")


def test_an_item_that_would_have_been_admitted_anyway_is_not_called_a_waiver(tmp_path):
    """A P0 under a P0 floor clears on its own merits. Announcing a waiver there
    would print on ordinary dispatches and train the reader to skip the word —
    which is how the loud path stops being loud."""
    v = _v(tmp_path, 75)
    assert v.admits(_item("st-1", priority=0), "ellie") == ""
    assert not v.waives(_item("st-1", priority=0), "ellie")


# --- config parsing -----------------------------------------------------------

def test_a_bare_string_is_refused_with_the_fix(tmp_path):
    """`exempt = "ellie"` is the typo an operator writes first. Iterating it
    would exempt nobody while every LETTER looked like a name in a dump."""
    text = EXEMPT.replace('exempt = ["ellie"]', 'exempt = "ellie"')
    (tmp_path / "shantytown.toml").write_text(text)
    _, err = config.load_or_default(tmp_path)
    assert err and "ARRAY" in err
    assert 'exempt = ["ellie"]' in err, "a refusal with no remedy is a dead end"


def test_a_repeated_name_is_refused(tmp_path):
    text = EXEMPT.replace('exempt = ["ellie"]', 'exempt = ["ellie", "ellie"]')
    (tmp_path / "shantytown.toml").write_text(text)
    _, err = config.load_or_default(tmp_path)
    assert err and "ellie" in err


@pytest.mark.parametrize("bad", ['[""]', '[123]', '["  "]'])
def test_an_empty_or_non_string_entry_is_refused(tmp_path, bad):
    text = EXEMPT.replace('exempt = ["ellie"]', f'exempt = {bad}')
    (tmp_path / "shantytown.toml").write_text(text)
    _, err = config.load_or_default(tmp_path)
    assert err, f"exempt = {bad} was accepted"


def test_exempt_is_absent_by_default_and_a_typo_is_still_refused(tmp_path):
    """The default is the empty tuple, so a deployment that has never heard of
    this feature is unaffected — and adding the key to the allowed set must not
    have opened the table to arbitrary keys."""
    (tmp_path / "shantytown.toml").write_text(SPOKEN)
    assert config.load(tmp_path).governor.exempt == ()
    (tmp_path / "shantytown.toml").write_text(
        SPOKEN.replace('relax_margin = 5', 'relax_margin = 5\nexemt = ["x"]'))
    _, err = config.load_or_default(tmp_path)
    assert err and "exemt" in err, "an unknown key must still be refused"


# --- end to end, through the real Dispatcher ----------------------------------

def _dispatcher(tmp_path, pct, item, agent=ORDINARY):
    v = _gov(tmp_path, pct, text=EXEMPT).evaluate()
    tracker = _Tracker(item)
    panes = _Panes(live={f"p-{agent.name}"})
    d = Dispatcher(_Registry([agent]), tracker, panes, governor=v.admits)
    d.panes.send = lambda pane, text: None
    d.verify = lambda pane, item_id: True
    return d, tracker


def test_the_dispatcher_refuses_a_non_exempt_agent_under_the_floor(tmp_path):
    d, tracker = _dispatcher(tmp_path, 75,
                             WorkItem(id="st-2", title="t", priority=1))
    with pytest.raises(GovernorRefused):
        d.go("st-2", ORDINARY.name)
    assert tracker.updates == [], "a refusal must write NOTHING"


def test_the_dispatcher_admits_an_exempt_agent_under_the_floor(tmp_path):
    """The deliverable, driven through the same path `st go` uses — the agent
    name reaches the gate, which is the change dispatch.py had to make."""
    from shantytown.protocols import Agent
    ellie = Agent(name="ellie", pane="p-ellie",
                  **{k: v for k, v in vars(ORDINARY).items()
                     if k not in ("name", "pane")})
    d, tracker = _dispatcher(tmp_path, 75,
                             WorkItem(id="st-3", title="t", priority=1),
                             agent=ellie)
    d.go("st-3", "ellie")
    assert tracker.updates == [("st-3", {"status": "in_progress",
                                         "assignee": "ellie"})]
