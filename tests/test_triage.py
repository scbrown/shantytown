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
    """'running' is not health. exists() is necessary, not sufficient.

    A wedge is the SESSION being gone, not the agent printing something ugly.
    """
    d = triage(panes("[Process completed]"), "%1", "work")
    assert d.action is Action.RESTART
    assert "[Process completed]" in d.inputs["marker"]


def test_a_traceback_is_not_a_wedge():
    """REGRESSION (aegis-hd2q). This used to RESTART, and RESTART relaunches.

    Agents print tracebacks constantly — a failing test prints one. Measured: a
    healthy, idle agent whose pane showed a ZeroDivisionError and then "I'll fix
    that now" was classified wedged and would have been killed mid-work.
    """
    screen = (
        '● I ran the test and it failed:\n'
        '  Traceback (most recent call last):\n'
        '    File "x.py", line 1, in <module>\n'
        '  ZeroDivisionError: division by zero\n'
        "● I'll fix that now.\n"
        "❯ \n"
        "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents\n"
    )
    assert triage(panes(screen), "%1", "work").action is Action.NUDGE


def test_clear_when_high_context_and_unrelated():
    """REGRESSION (aegis-hd2q): the CLEAR branch must be reachable FROM A REAL PANE.

    This test used to synthesise a 500-line screen and assert CLEAR. It passed,
    and it was measuring nothing: Tmux.capture runs `capture-pane -p` with no
    -S, so a real pane yields ~24 lines. `len(screen) > 400` could never be
    true in production. The branch was dead and the test made it look alive —
    a check incapable of one of its outcomes (aegis-mt0r), in the file written
    to encode that lesson.
    So the screen here is 24 lines, the size a real capture actually is, and the
    signal is the one Claude Code itself prints.
    """
    screen = "\n".join(
        ["● working on kubernetes networking"] * 20
        + [
            "❯ ",
            "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents",
            "                  new task? /clear to save 737.6k tokens",
            "",
        ]
    )
    assert len(screen.splitlines()) < 30, "fixture must be pane-sized, not synthetic"
    d = triage(panes(screen), "%1", "restore the den dashboard service")
    assert d.action is Action.CLEAR
    assert d.inputs["context_k"] == 737.6


def test_context_high_is_reachable_from_a_real_capture():
    """The positive control for the branch above.

    If this ever fails, CLEAR has gone structurally unreachable again — which is
    how it shipped the first time, silently, while its unit test passed.
    """
    from shantytown.triage import context_high, context_tokens_k

    real_pane_sized = "❯ \n                  new task? /clear to save 737.6k tokens\n"
    assert context_tokens_k(real_pane_sized) == 737.6
    assert context_high(real_pane_sized) is True
    # and the old proxy's world: a big screen with no hint is NOT high
    assert context_high("\n".join("x" for _ in range(500))) is False


def test_high_context_but_RELATED_still_nudges():
    """The discriminating case: high context alone must NOT trigger CLEAR."""
    screen = "\n".join(
        ["● restore the den dashboard service step %d" % i for i in range(20)]
        + ["                  new task? /clear to save 737.6k tokens"]
    )
    d = triage(panes(screen), "%1", "restore the den dashboard service")
    assert d.action is Action.NUDGE, "CLEAR fired on RELATED work — it isn't discriminating"


def test_every_decision_is_inspectable():
    """Do not ship a confident heuristic you cannot inspect."""
    for screen, ex in [("$ ", True), ("esc to interrupt", True), ("", False)]:
        d = triage(panes(screen, ex), "%1", "w")
        assert d.inputs, f"{d.action} carried no inputs — unauditable"
        assert d.why
        assert "inputs:" in d.render()
