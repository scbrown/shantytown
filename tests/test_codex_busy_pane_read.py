"""A WORKING codex pane must not read idle, and a request state must not blank
the pane read.

aegis-4j4ypk. `st crew` and `st go` disagreed on the same evidence three times on
2026-09-02 — st go REFUSED on marker 'esc to interrupt' while st crew reported
the same pane free. Two independent defects produced it, and they need separate
tests because they fail for unrelated reasons.

The shared consequence is the expensive one: a coordinator dispatches into a live
turn. A false BUSY costs one skipped dispatch that the next pass retries; a false
IDLE interrupts work in flight.
"""
from __future__ import annotations
import json

from shantytown import triage


# --- 1. the window: codex paints its marker ABOVE the input box --------------

def _codex_working(continuation_lines: int) -> str:
    """A real ian footer (captured live), parameterised on the one thing that
    varies: how many lines codex's tool-call continuation occupies. It truncates
    with `…` when it fits and wraps when it does not."""
    b = ['• Called homelab.forgejo_watch({"task_id":12717})',
         "  └ … Task 12717: running", "",
         "• Waiting for background terminal (2m 40s • esc to interrupt) · 1 background te…"]
    b += ["  └ scripts/ansible-apply-dispatch.sh --limit monitoring_servers --tags"] * continuation_lines
    b += ["", "", "› Ask Codex to do anything", "", "  gpt-5.6-sol default · ~/gt"]
    return "\n".join(b) + "\n" * 6          # tmux pads the capture to pane height


def test_a_working_codex_pane_is_busy_however_tall_its_tool_block():
    """The regression. With an 8-line window off the BOTTOM this went False at
    3 continuation lines — measured headroom on live panes was 1 (ian) and 2
    (gennaro, malcolm), so ordinary output was one wrapped line from a misread."""
    for n in range(1, 6):
        assert triage.mid_flight(_codex_working(n)) is True, (
            f"a codex pane with a {n}-line tool continuation read as NOT busy; "
            f"a coordinator would dispatch into a live turn")


def test_a_genuinely_idle_codex_prompt_still_reads_free():
    """The positive control the bead asks for. Widening a busy-detector until
    everything looks busy would pass the test above and break dispatch entirely."""
    idle = "• Done — closed aegis-1234.\n\n› Ask Codex to do anything\n\n  gpt-5.6-sol default\n"
    assert triage.mid_flight(idle) is False


def test_scrollback_mentioning_the_marker_is_still_not_busy():
    """The property the old fixed window was protecting, kept. `_tail`'s
    docstring is right that this very file contains 'esc to interrupt', so an
    agent READING triage.py must not read as permanently busy."""
    talking = ("I found 'esc to interrupt' in triage.py and it is the marker\n"
               + "\n".join(["some other output"] * 20)
               + "\n› Ask Codex to do anything\n\n  gpt-5.6-sol default\n")
    assert triage.mid_flight(talking) is False


def test_claude_footer_below_the_prompt_is_unaffected():
    """Claude paints its marker BELOW the input box. The anchor must not move
    that case — this is the shape the fixed window always handled."""
    claude = ("❯ \n"
              "  ⏵⏵ bypass permissions on · 1 shell · esc to interrupt\n")
    assert triage.mid_flight(claude) is True


# --- 2. the columns: a request state must not blank a pane observation -------

class _Panes:
    def __init__(self, screens):
        self._s = screens

    def exists(self, pane):
        return pane in self._s

    def capture(self, pane, attrs=False):
        return self._s[pane]

    def cmdline(self, pane):
        return None


class _Runtime:
    def shows_ready_ui(self, plain):
        return True


def _card(name, pane, role="worker"):
    class C:
        pass
    c = C()
    c.name, c.pane, c.role = name, pane, role
    c.harness = "codex"
    return c


def test_cycle_blocked_still_reports_the_work_verdict(monkeypatch):
    """MEASURED live: `st crew` printed `ian  cycle-blocked  —` while ian's pane
    read "Waiting for background terminal (2m 53s • esc to interrupt)" and it was
    mid-way through an ansible apply.

    A refused cycle is STICKY — the request stays pending until somebody commits
    a tree — so this is not a momentary blind spot. `—` means NOT LOOKED AT, and
    printing it for a pane we could have read puts a false unknown in the column
    a coordinator uses to decide whether to interrupt.
    """
    from shantytown import cli
    monkeypatch.setattr(cli, "asks_a_question", lambda rt, plain: False, raising=False)
    monkeypatch.setattr(cli, "auth_expired", lambda rt, plain: False, raising=False)
    panes = _Panes({"shanty-ian": _codex_working(1)})
    rows = list(cli._crew_states([_card("ian", "shanty-ian")], panes, _Runtime(),
                                 cycle_blocked={"ian"}))
    _, state, work, _ = rows[0]
    assert state == "cycle-blocked"
    assert work.startswith(triage.BUSY), (
        f"a cycle-blocked agent that is demonstrably working reported {work!r}")


def test_cycle_blocked_with_a_dead_pane_still_reports_nothing(monkeypatch):
    """The control for the fix above: `—` remains correct when there is genuinely
    nothing to look at. Without this, 'always read the pane' could be satisfied
    by inventing a verdict for an agent that is gone."""
    from shantytown import cli
    monkeypatch.setattr(cli, "asks_a_question", lambda rt, plain: False, raising=False)
    monkeypatch.setattr(cli, "auth_expired", lambda rt, plain: False, raising=False)
    rows = list(cli._crew_states([_card("kelly", "shanty-kelly")], _Panes({}),
                                 _Runtime(), cycle_blocked={"kelly"}))
    _, state, work, _ = rows[0]
    assert state == "cycle-blocked"
    assert work == "—"
