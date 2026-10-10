"""workflow — the pure prioritization logic. No I/O; runs without any backend.

Mirrors the style of test_tier/test_prime: build realistic state, observe the
rendered/returned finding, assert determinism.
"""
from __future__ import annotations

from shantytown import workflow as wf
from shantytown.events import StopEvent
from shantytown.protocols import Agent, WorkItem


class _Panes:
    def __init__(self, up):
        self._up = set(up)

    def exists(self, pane):
        return pane in self._up


def test_classify_derives_state_from_pane_and_plate():
    agents = [
        Agent("ellie", "worker", "lead", "p-ellie"),   # up + item  -> WORKING
        Agent("bran", "worker", "lead", "p-bran"),      # up, no item -> IDLE
        Agent("arya", "worker", "lead", "p-arya"),      # pane down   -> STOPPED
        Agent("nym", "worker", "lead", None),           # no pane     -> NO_PANE
    ]
    panes = _Panes({"p-ellie", "p-bran"})
    plates = {"ellie": WorkItem("st-1", "x", "in_progress", "ellie")}
    by = {c.agent: c for c in wf.classify(agents, panes, plates.get)}
    assert by["ellie"].state == wf.AgentState.WORKING
    assert by["bran"].state == wf.AgentState.IDLE
    assert by["arya"].state == wf.AgentState.STOPPED
    assert by["nym"].state == wf.AgentState.NO_PANE


def test_classify_with_no_plate_reader_reads_every_plate_empty():
    agents = [Agent("bran", "worker", "lead", "p-bran")]
    by = {c.agent: c for c in wf.classify(agents, _Panes({"p-bran"}), None)}
    assert by["bran"].state == wf.AgentState.IDLE      # honest empty, not a guess


def test_prioritize_orders_rose_then_stopped_then_idle_and_omits_working():
    cands = [
        wf.Candidate("w", "worker", wf.AgentState.WORKING),
        wf.Candidate("i", "worker", wf.AgentState.IDLE),
        wf.Candidate("s", "worker", wf.AgentState.STOPPED),
        wf.Candidate("e", "worker", wf.AgentState.STOPPED, rose=True,
                     stop_reason="needs-decision"),
    ]
    steps = wf.prioritize(cands).steps
    assert [s.candidate.agent for s in steps] == ["e", "s", "i"]
    assert [s.action for s in steps] == ["decide", "re-dispatch", "assign work"]


def test_prioritize_breaks_ties_by_weight_then_name():
    cands = [
        wf.Candidate("low", "worker", wf.AgentState.STOPPED, weight=1.0),
        wf.Candidate("high", "worker", wf.AgentState.STOPPED, weight=9.0),
    ]
    assert [s.candidate.agent for s in wf.prioritize(cands).steps] == ["high", "low"]


def test_fold_events_attaches_reason_and_adds_an_uncarded_agent():
    cands = [wf.Candidate("arya", "worker", wf.AgentState.STOPPED)]
    events = [
        StopEvent(id="ev-1", to="admin", frm="arya", reason="too-large", rose=False),
        StopEvent(id="ev-2", to="admin", frm="ghost", reason="lead-unreachable",
                  rose=True),
    ]
    by = {c.agent: c for c in wf.fold_events(cands, events)}
    assert by["arya"].stop_reason == "too-large"
    assert by["ghost"].rose is True
    assert by["ghost"].state == wf.AgentState.STOPPED   # not dropped


def test_render_is_empty_when_nothing_is_actionable():
    assert wf.prioritize([wf.Candidate("w", "worker", wf.AgentState.WORKING)]).render() == ""


def test_render_lists_numbered_priorities_with_reasons():
    item = WorkItem("st-42", "x", "in_progress", "ellie")
    cands = [
        wf.Candidate("ellie", "worker", wf.AgentState.STOPPED, item=item, weight=37.0),
        wf.Candidate("bran", "worker", wf.AgentState.IDLE),
    ]
    text = wf.prioritize(cands).render()
    assert "PRIORITIZE" in text
    assert "1. re-dispatch ellie — STOPPED, was on st-42 (blast radius 37)" in text
    assert "2. assign work bran — IDLE, empty plate" in text


def test_prioritize_is_deterministic():
    cands = [wf.Candidate(n, "worker", wf.AgentState.IDLE) for n in ("c", "a", "b")]
    once = [s.candidate.agent for s in wf.prioritize(cands).steps]
    twice = [s.candidate.agent for s in wf.prioritize(cands).steps]
    assert once == twice == ["a", "b", "c"]


def test_failed_plate_roster_is_unknown_not_nine_empty_plates():
    agents = [Agent(f"worker{i}", "worker", "lead", f"p{i}") for i in range(9)]
    def failed(who):
        raise RuntimeError("owner listing unreadable")
    candidates = wf.classify(agents, _Panes({f"p{i}" for i in range(9)}), failed)
    rendered = wf.prioritize(candidates).render()
    assert "assign work" not in rendered
    assert "empty plate" not in rendered
    assert "PLATE UNKNOWN" in rendered
    assert all(c.state == wf.AgentState.PLATE_UNKNOWN for c in candidates)
    assert all(a.name in rendered for a in agents)

def test_unknown_plate_does_not_hide_healthy_empty_or_held_plate():
    agents = [Agent(name, "worker", "lead", name) for name in ("bad", "empty", "held")]
    def reader(who):
        if who == "bad":
            raise RuntimeError("listing failed")
        return WorkItem("held-id", "work", "in_progress", who) if who == "held" else None
    rendered = wf.prioritize(wf.classify(agents, _Panes({a.name for a in agents}), reader)).render()
    assert "assign work empty" in rendered
    assert "assign work bad" not in rendered
    assert "assign work held" not in rendered
    assert "PLATE UNKNOWN" in rendered


def test_failed_plate_on_stopped_worker_does_not_advise_redispatch():
    agents = [Agent("worker", "worker", "lead", "pane")]
    def failed(who):
        raise RuntimeError("owner listing unreadable")
    candidates = wf.classify(agents, _Panes(set()), failed)
    assert candidates[0].state == wf.AgentState.STOPPED
    rendered = wf.prioritize(candidates).render()
    assert "re-dispatch" not in rendered
    assert "PLATE UNKNOWN" in rendered
    assert "worker" in rendered

def test_failed_plate_preserves_operator_retirement_no_pane_and_rose():
    def failed(who):
        raise RuntimeError("owner listing unreadable")
    for kind in ("operator", "retired", "no-pane", "stopped"):
        a = Agent("worker", "worker", "lead", None if kind == "no-pane" else "pane",
                  retired=kind == "retired")
        candidates = wf.classify([a], _Panes(set()), failed,
                                 stopped=lambda _: 10 if kind == "operator" else None, now=20)
        expected = {"operator": wf.AgentState.STOPPED_BY_OPERATOR,
                    "retired": wf.AgentState.RETIRED, "no-pane": wf.AgentState.NO_PANE,
                    "stopped": wf.AgentState.STOPPED}[kind]
        assert candidates[0].state == expected
        text = wf.prioritize(candidates).render()
        assert "PLATE UNKNOWN" in text
        assert "re-dispatch" not in text and "assign work" not in text
        candidates[0].rose = True
        candidates[0].stop_reason = "review blocker"
        risen = wf.prioritize(candidates).render()
        assert "decide worker" in risen
        assert "PLATE UNKNOWN" in risen
        assert "re-dispatch" not in risen and "assign work" not in risen

def test_stopped_complete_plate_retains_redispatch():
    a = Agent("worker", "worker", "lead", "pane")
    for item in (None, WorkItem("held-id", "work", "in_progress", "worker")):
        text = wf.prioritize(wf.classify([a], _Panes(set()), lambda _: item)).render()
        assert "re-dispatch worker" in text
        assert "PLATE UNKNOWN" not in text
