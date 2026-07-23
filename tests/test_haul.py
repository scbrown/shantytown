"""The HAUL advance: a worker's assigned queue feeds ITSELF at its own stop.

The design bead's core contract, each clause a test: advance only on evidence
the anchor finished (a stop is a turn boundary, not an idle agent); the next
bead arrives as the Stop-hook block reason (the same model-reaching protocol
drain and the Rule Zero gate use); the 600k handoff line (60% of the window)
stops the feed and instructs the reset instead; everything fails OPEN — a
broken advance must never trap a worker at its own stop.
"""
from __future__ import annotations
import json

import pytest

from shantytown import stop_event
from shantytown.protocols import Agent


class _Reg:
    def __init__(self, cards):
        self._c = {a.name: a for a in cards}
    def get(self, name):
        return self._c[name]
    def all(self):
        return list(self._c.values())


class _Panes:
    def __init__(self, screens=None):
        self._screens = screens or {}
    def exists(self, pane):
        return pane in self._screens
    def capture(self, pane, history=0, attrs=False):
        return self._screens.get(pane, "")


WORKER = Agent(name="billy", role="worker", pane="p-b")
SATURATED_PANE = ("❯ \n"
                  "                  new task? /clear to save 650.0k tokens\n"
                  "  ⏵⏵ bypass permissions on (shift+tab to cycle)")


def _bd(monkeypatch, ready=None, in_progress=None, fail=False, claims=None):
    """Stub the two bd reads + the claim write the advance makes."""
    def fake(args, cwd):
        if fail:
            raise RuntimeError("bd unreachable")
        if args[0] == "ready":
            return ready or []
        if args[0] == "list":
            return in_progress or []
        if args[0] == "update":
            (claims if claims is not None else []).append(args[1])
            return []
        raise AssertionError(args)
    monkeypatch.setattr(stop_event, "_bd_json", fake)


def _run(monkeypatch, capsys, reg=None, panes=None, **bd):
    _bd(monkeypatch, **bd)
    rc = stop_event._haul(reg or _Reg([WORKER]), panes or _Panes({"p-b": "❯ "}),
                          "billy", None)
    out = capsys.readouterr().out.strip()
    return rc, (json.loads(out) if out else None)


def test_anchor_closed_and_queue_ready_feeds_the_next_bead(monkeypatch, capsys):
    claims = []
    rc, block = _run(monkeypatch, capsys, claims=claims,
                     ready=[{"id": "aegis-2", "title": "next up",
                             "assignee": "beads_aegis/crew/billy"}])
    assert rc == 0
    assert block["decision"] == "block"
    assert "aegis-2" in block["reason"] and "HAUL" in block["reason"]
    assert "coordinator was not pinged" in block["reason"]
    assert claims == ["aegis-2"], "the fed bead is claimed in_progress"


def test_an_active_anchor_is_a_turn_boundary_allow_silently(monkeypatch, capsys):
    """The w9z1 correction: mid-work stops are normal. Halting or feeding here
    would fire at every turn end."""
    rc, block = _run(monkeypatch, capsys,
                     ready=[{"id": "aegis-2", "assignee": "billy"}],
                     in_progress=[{"id": "aegis-1", "assignee": "billy"}])
    assert rc == 0 and block is None


def test_an_empty_queue_is_normal_idle_flow(monkeypatch, capsys):
    rc, block = _run(monkeypatch, capsys, ready=[])
    assert rc == 0 and block is None


def test_someone_elses_beads_are_not_my_haul(monkeypatch, capsys):
    rc, block = _run(monkeypatch, capsys,
                     ready=[{"id": "aegis-9", "assignee": "crew/kelly"}])
    assert rc == 0 and block is None


def test_past_the_600k_line_the_advance_instructs_handoff_not_food(monkeypatch, capsys):
    """Stiwi's line: 60% of the 1M window. Between beads the context is
    disposable by construction — so past the line the block instructs
    checkpoint + /clear and feeds NOTHING; the haul resumes on fresh context."""
    claims = []
    rc, block = _run(monkeypatch, capsys, claims=claims,
                     panes=_Panes({"p-b": SATURATED_PANE}),
                     ready=[{"id": "aegis-2", "assignee": "billy"}])
    assert rc == 0
    assert "HANDOFF" in block["reason"] and "650" in block["reason"]
    assert "aegis-2" not in block["reason"], "past the line, nothing is fed"
    assert claims == [], "nothing is claimed either"


def test_unknown_context_depth_never_triggers_the_handoff(monkeypatch, capsys):
    """None is not over-the-line — unknown never blocks the feed (the
    None-is-not-zero house rule, handoff edition)."""
    rc, block = _run(monkeypatch, capsys,
                     ready=[{"id": "aegis-2", "assignee": "billy"}])
    assert "HAUL:" in block["reason"] and "HANDOFF" not in block["reason"]


def test_a_non_worker_never_hauls(monkeypatch, capsys):
    rc, block = _run(monkeypatch, capsys,
                     reg=_Reg([Agent(name="billy", role="lead", pane="p-b")]),
                     ready=[{"id": "aegis-2", "assignee": "billy"}])
    assert rc == 0 and block is None


def test_bd_failure_fails_open_never_traps_the_stop(monkeypatch, capsys):
    rc, block = _run(monkeypatch, capsys, fail=True)
    assert rc == 0 and block is None


def test_a_failed_claim_still_feeds(monkeypatch, capsys):
    """The claim is best-effort: the instruction tells the agent to read the
    bead either way, and a feed that dies on a tracker hiccup would stall the
    haul over bookkeeping."""
    def fake(args, cwd):
        if args[0] == "ready":
            return [{"id": "aegis-2", "assignee": "billy"}]
        if args[0] == "list":
            return []
        raise RuntimeError("update refused")
    monkeypatch.setattr(stop_event, "_bd_json", fake)
    stop_event._haul(_Reg([WORKER]), _Panes({"p-b": "❯ "}), "billy", None)
    out = capsys.readouterr().out
    assert "aegis-2" in out, "feed survives a failed claim"
