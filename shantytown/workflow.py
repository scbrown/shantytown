"""workflow — a prioritized workflow over fleet state. Pure, no I/O.

At the administrator's stop, the drain (stop_event.py) enriches the stop events
routed to it with a PRIORITIZED WORKFLOW built from fleet state:

  - a STOPPED worker (has a pane, pane is down)  -> re-dispatch / investigate
  - an IDLE worker    (pane up, empty plate)      -> has capacity, assign work
  - a risen escalation (rose past a down lead)     -> the admin must decide

This module is the PURE logic — `classify()` over (agents, panes, plate),
`fold_events()` to attach stop reasons, `prioritize()` to order. A `Ranker`
(policy.py) may weight candidates by structural blast radius; when it can't, the
rule-based order stands. Nothing here touches the disk, a socket, or a backend —
`test_workflow.py` asserts that the same way prime's purity is asserted.

Shantytown ORCHESTRATES: the workflow both informs the administrator and can
drive action (create/dispatch work) — autonomous assignment is fine. It rides the
drain's BLOCK-ONCE stop events, so it never fires on its own and a persistently-
idle fleet cannot re-block the admin every stop (a real correctness property,
kept regardless of the orchestration stance).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .protocols import Agent, Panes, WorkItem


class AgentState(Enum):
    """What an agent is, derived from (pane, plate). NO_PANE is 'could not tell',
    never 'down' — the same three-way honesty `crew` keeps (cli.py:_cmd_crew)."""
    STOPPED = "stopped"     # has a pane, pane is down
    IDLE = "idle"           # pane up, empty plate
    WORKING = "working"     # pane up, holding a plate item
    NO_PANE = "no-pane"     # no pane on the card — cannot tell


@dataclass
class Candidate:
    """One agent the admin might prioritize, plus the signals that rank it.
    Mutable so a ranker can fold in a weight and fold_events a stop reason."""
    agent: str
    role: str
    state: AgentState
    item: WorkItem | None = None      # what it holds / was working on
    stop_reason: str | None = None    # from a routed StopEvent, if any
    rose: bool = False                # did its stop rise past a down lead?
    weight: float = 0.0               # structural weight (blast radius); 0 = none
    why: str = ""                     # ranker's note


@dataclass
class WorkflowStep:
    rank: int
    candidate: Candidate
    action: str                       # advisory verb


@dataclass
class PrioritizedWorkflow:
    steps: list[WorkflowStep]

    def render(self) -> str:
        """The block appended into the admin's drain prompt. '' when nothing is
        actionable — an empty workflow adds no lines rather than an empty header."""
        if not self.steps:
            return ""
        lines = ["  PRIORITIZE"]
        for s in self.steps:
            lines.append(f"    {s.rank}. {s.action} {s.candidate.agent} — "
                         f"{_describe(s.candidate)}")
        return "\n".join(lines)


def classify(
    agents: list[Agent],
    panes: Panes,
    plate: Callable[[str], WorkItem | None] | None = None,
) -> list[Candidate]:
    """Derive each agent's state from its pane and plate. Reads only — `panes`
    and `plate` are the same readers `crew` and `prime` already use. `plate` may
    be None (no tracker wired) — then every plate reads empty, honestly."""
    out: list[Candidate] = []
    for a in agents:
        item = plate(a.name) if plate else None
        if a.pane is None:
            state = AgentState.NO_PANE
        elif not panes.exists(a.pane):
            state = AgentState.STOPPED
        elif item is None:
            state = AgentState.IDLE
        else:
            state = AgentState.WORKING
        out.append(Candidate(agent=a.name, role=a.role, state=state, item=item))
    return out


def fold_events(candidates: list[Candidate], events) -> list[Candidate]:
    """Attach each drained StopEvent's reason/rise to its candidate. A stop from
    an agent absent from the crew listing (no card) is added as a STOPPED
    candidate rather than dropped — the admin still sees it."""
    by_name = {c.agent: c for c in candidates}
    out = list(candidates)
    for e in events:
        c = by_name.get(e.frm)
        if c is None:
            c = Candidate(agent=e.frm, role="worker", state=AgentState.STOPPED)
            by_name[e.frm] = c
            out.append(c)
        c.stop_reason = e.reason
        c.rose = e.rose or c.rose
    return out


def prioritize(candidates: list[Candidate]) -> PrioritizedWorkflow:
    """Order the actionable candidates. Rule-based baseline (deterministic):
    risen escalations first, then STOPPED, then IDLE; ties by weight desc then
    name. WORKING / NO_PANE are not actionable right now and are omitted."""
    actionable = [c for c in candidates if _tier(c) < _OMIT]
    actionable.sort(key=lambda c: (_tier(c), -c.weight, c.agent))
    steps = [WorkflowStep(rank=i + 1, candidate=c, action=_action(c))
             for i, c in enumerate(actionable)]
    return PrioritizedWorkflow(steps)


_OMIT = 9


def _tier(c: Candidate) -> int:
    if c.rose:
        return 0
    if c.state == AgentState.STOPPED:
        return 1
    if c.state == AgentState.IDLE:
        return 2
    return _OMIT


def _action(c: Candidate) -> str:
    if c.rose:
        return "decide"
    if c.state == AgentState.STOPPED:
        return "re-dispatch"
    if c.state == AgentState.IDLE:
        return "assign work"
    return "review"


def _describe(c: Candidate) -> str:
    if c.rose:
        base = f"escalation ({c.stop_reason})" if c.stop_reason else "escalation"
    elif c.state == AgentState.STOPPED:
        base = "STOPPED"
        if c.item:
            base += f", was on {c.item.id}"
        elif c.stop_reason:
            base += f" ({c.stop_reason})"
    elif c.state == AgentState.IDLE:
        base = "IDLE, empty plate"
    else:
        base = c.state.value
    if c.weight > 0:
        base += f" (blast radius {int(c.weight)})"
    return base
