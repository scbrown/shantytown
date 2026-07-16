"""A triage that has only ever said NUDGE is not triage.

vision.md item 3: demonstrated in BOTH directions — one nudge that lands, one
target correctly refused or cleared. So every branch here has a test, and the
RED must be a TRUE negative: we assert WHY it fired, not just that it did.
"""
from __future__ import annotations
import pytest

from shantytown.tmux import NullPanes
from shantytown.triage import Action, triage


def panes(screen="$ ", exists=True):
    p = NullPanes(screen=screen)
    p._exists = exists
    return p


def test_nudge_when_healthy():
    d = triage(panes("$ idle prompt"), "%1", "restore the den service")
    assert d.action is Action.NUDGE
    assert d.inputs["context_high"] is False


def test_refuse_when_mid_flight():
    """REFUSE is a real outcome. Sending interrupts in-flight work."""
    d = triage(panes("Thinking… (12s · 4.2k tokens · esc to interrupt)"), "%1", "new work")
    assert d.action is Action.REFUSE
    # the RED must be a TRUE negative — we know WHY it fired
    assert d.inputs["marker"] == "esc to interrupt"


def test_restart_when_no_session():
    d = triage(panes(exists=False), "%9", "work")
    assert d.action is Action.RESTART
    assert d.inputs["exists"] is False


def test_restart_when_wedged():
    """'running' is not health. exists() is necessary, not sufficient."""
    d = triage(panes("Traceback (most recent call last):\n  File x"), "%1", "work")
    assert d.action is Action.RESTART
    assert "Traceback" in d.inputs["marker"]


def test_clear_when_high_context_and_unrelated():
    screen = "\n".join(f"line about kubernetes networking {i}" for i in range(500))
    d = triage(panes(screen), "%1", "restore the den dashboard service")
    assert d.action is Action.CLEAR
    assert d.inputs["screen_lines"] > 400


def test_high_context_but_RELATED_still_nudges():
    """The discriminating case: high context alone must NOT trigger CLEAR."""
    screen = "\n".join(f"restore the den dashboard service step {i}" for i in range(500))
    d = triage(panes(screen), "%1", "restore the den dashboard service")
    assert d.action is Action.NUDGE, "CLEAR fired on RELATED work — it isn't discriminating"


def test_every_decision_is_inspectable():
    """Do not ship a confident heuristic you cannot inspect."""
    for screen, ex in [("$ ", True), ("esc to interrupt", True), ("", False)]:
        d = triage(panes(screen, ex), "%1", "w")
        assert d.inputs, f"{d.action} carried no inputs — unauditable"
        assert d.why
        assert "inputs:" in d.render()
