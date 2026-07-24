"""A DECISION-GATED BEAD IS NOT IMPLEMENTER WORK (aegis-2og7d).

The message defect's sibling. The haul advance, the Rule Zero feed and
coordinator dispatch all decide "may a worker be handed this?", and a bead
whose completion is defined by a HUMAN decision must never be auto-fed with
"execute and close" — exactly the shape of the message-on-a-plate bug, one
level up, and here with teeth: the haul fed two one-way-door forge-token
revokes on Stiwi's PERSONAL account (aegis-nmc0 -> weaver, aegis-w4nn ->
harding) each "read it and execute; close it when done", during the live apz9
approval-forging incident. Both agents held; the trap only has to win once.

One shared predicate (inbox.is_decision) across all three readers, asserted
against the predicate itself, so they cannot drift apart again.
"""
from __future__ import annotations

from shantytown.feed_check import dispatchable, hauls
from shantytown.inbox import is_decision
from shantytown.stop_event import _assigned_to


def _bead(bid, title, labels=None, assignee="beads_aegis/crew/weaver", status="open"):
    return {"id": bid, "title": title, "labels": labels or [],
            "assignee": assignee, "status": status}


# --- the haul advance -------------------------------------------------------

def test_haul_ready_set_excludes_decision_beads():
    got = _assigned_to("weaver", [
        _bead("st-1", "Revoke 5 scope=all forge tokens", labels=["decision-needed", "security"]),
        _bead("st-2", "Wire the family ratings page", labels=["frontend"]),
    ])
    assert [b["id"] for b in got] == ["st-2"], (
        "the haul would feed a human-decision bead as 'execute and close' work")


def test_haul_still_feeds_real_work():
    got = _assigned_to("weaver", [_bead("st-9", "Fix the COALESCE scan bug", labels=["bug"])])
    assert [b["id"] for b in got] == ["st-9"]


def test_the_whole_decision_label_family_is_excluded():
    """The store's vocabulary is fragmented; matching only one spelling would
    leave the others (7 decision-stiwi beads today) still auto-fed."""
    fam = ["decision-needed", "decision-stiwi", "decision", "needs-stiwi-decision"]
    got = _assigned_to("weaver", [_bead(f"st-{i}", "gate", labels=[lbl])
                                  for i, lbl in enumerate(fam)])
    assert got == [], f"a decision-family label slipped through: {got}"


def test_haul_matches_the_predicate_exactly():
    """THE POINT: one predicate, asserted against inbox.is_decision itself, so
    haul/feed/dispatch cannot disagree about what a worker may be handed."""
    cases = [["decision-needed"], ["security"], ["decision-stiwi", "forgejo"],
             [], ["needs-stiwi-decision"], ["frontend", "decision"]]
    beads = [_bead(f"st-{i}", "x", labels=ls) for i, ls in enumerate(cases)]
    kept = {b["id"] for b in _assigned_to("weaver", beads)}
    assert kept == {f"st-{i}" for i, ls in enumerate(cases) if not is_decision(ls)}


def test_a_decision_bead_is_not_an_active_anchor():
    """_assigned_to also answers 'am I mid-work?'. A decision bead counted as an
    active anchor would suppress the advance for a worker that is genuinely free
    (it is not theirs to execute) — the opposite failure, same root."""
    assert _assigned_to("weaver", [
        _bead("st-1", "decide", labels=["decision-needed"], status="in_progress")]) == []


# --- the Rule Zero feed gate ------------------------------------------------

def test_a_worker_holding_only_decision_beads_is_not_self_feeding():
    got = hauls([
        _bead("st-1", "a", labels=["decision-needed"]),
        _bead("st-2", "b", labels=["decision-stiwi"]),
    ])
    assert got == {}, f"weaver looks self-feeding on decision beads alone: {got}"


def test_a_worker_with_real_ready_work_is_still_self_feeding():
    got = hauls([_bead("st-1", "d", labels=["decision-needed"]),
                 _bead("st-2", "Real queued work", labels=["bug"])])
    assert got == {"weaver": ["st-2"]}


# --- coordinator dispatch ---------------------------------------------------

def test_dispatchable_skips_unassigned_decision_beads():
    """An UNASSIGNED decision bead must not be handed to a worker to execute
    either — the coordinator route is the same class of mistake."""
    got = dispatchable(set(), [
        _bead("st-1", "Revoke tokens", labels=["decision-needed"], assignee=None),
        _bead("st-2", "Unowned real work", labels=["bug"], assignee=None),
    ])
    assert got == [("st-2", "Unowned real work")]
