"""workflow — a prioritized workflow over fleet state. Pure, no I/O.

At the administrator's stop, the drain (stop_event.py) enriches the stop events
routed to it with a PRIORITIZED WORKFLOW built from fleet state:

  - a STOPPED worker (has a pane, pane is down)  -> re-dispatch / investigate
  - an IDLE worker    (pane up, empty plate)      -> has capacity, assign work
  - a risen escalation (rose past a down lead)     -> the admin must decide

A DOWN AGENT IS NOT AUTOMATICALLY A FAULT (GitHub #29). Three states here are
DELIBERATE and none belongs on a re-dispatch list: a RETIRED card, a pane killed by
`st stop` (stopped.py), and a fleet that is STOOD DOWN. The measured bug: an
operator out of usage credits stopped
nine of eleven crew on instruction, and every surface then told them to put all
nine back — "re-dispatch felix — STOPPED" nine times. A mechanism that can only
ever say "do more" trains operators to ignore it, which costs the times it is
right. Suppression is ANNOUNCED, never silent (render()), for the same reason
Rule Zero announces its own yield: a list that quietly stops having entries is
indistinguishable from one that is broken.

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

from dataclasses import dataclass, field
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
    RETIRED = "retired"     # the CARD says deliberately stopped — down BY INTENT
    STOPPED_BY_OPERATOR = "stopped-by-operator"   # `st stop` said so (stopped.py).
                            # Down by intent, but UNLIKE retired it may come back —
                            # tend still respawns it. So it is reported as a fact,
                            # never demanded as a fix.


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
    stopped_ago: float | None = None  # seconds since `st stop` (STOPPED_BY_OPERATOR)


@dataclass
class WorkflowStep:
    rank: int
    candidate: Candidate
    action: str                       # advisory verb


@dataclass
class PrioritizedWorkflow:
    steps: list[WorkflowStep]
    held: list[Candidate] = field(default_factory=list)   # withheld by stand-down
    stood_down: bool = False
    deliberate: list[Candidate] = field(default_factory=list)   # `st stop`ped

    def render(self) -> str:
        """The block appended into the admin's drain prompt. '' when nothing is
        actionable — an empty workflow adds no lines rather than an empty header."""
        notes = [n for n in (self._stood_down_note(), self._deliberate_note()) if n]
        if not self.steps:
            return "\n".join(notes)        # may be '' — then no lines at all
        lines = ["  PRIORITIZE"]
        for s in self.steps:
            lines.append(f"    {s.rank}. {s.action} {s.candidate.agent} — "
                         f"{_describe(s.candidate)}")
        return "\n".join(lines + notes)

    def _stood_down_note(self) -> str:
        """What the stand-down WITHHELD, said out loud (#29).

        Only the stand-down is announced, and only while it is in force: it is a
        declared, temporary state with a way to clear it, so naming it is
        actionable. RETIRED cards are omitted SILENTLY — a retirement is steady
        state, and repeating a permanent fact at every single stop is the noise
        that gets whole blocks skipped. `st crew` and `st start` are where the
        retired roster is reported.
        """
        if not (self.stood_down and self.held):
            return ""
        down = sum(1 for c in self.held if c.state == AgentState.STOPPED)
        idle = len(self.held) - down
        breakdown = ", ".join(p for p in (f"{down} down" if down else "",
                                          f"{idle} idle" if idle else "") if p)
        return (f"  fleet STOOD DOWN — {len(self.held)} agent(s) left alone "
                f"({breakdown}). This is NOT a re-dispatch list: standing the "
                f"fleet down is the decision. Clear `[fleet] stood_down` to resume "
                f"dispatch.")

    def _deliberate_note(self) -> str:
        """The agents an operator stopped, stated as FACT and never as an item.

        Reported rather than omitted, because unlike a retirement an `st stop` is
        not "and do not bring it back" — the admin should know the fleet is short,
        and by whose hand. It carries no rank and no imperative verb: the whole
        defect was a mechanism telling an operator to undo a shutdown they had just
        been instructed to perform.
        """
        if not self.deliberate:
            return ""
        who = ", ".join(f"{c.agent}{f' ({_ago(c.stopped_ago)})' if c.stopped_ago else ''}"
                        for c in self.deliberate)
        return (f"  {len(self.deliberate)} agent(s) STOPPED BY AN OPERATOR, not "
                f"faults: {who}. `st new <agent>` if one is wanted back.")


def classify(
    agents: list[Agent],
    panes: Panes,
    plate: Callable[[str], WorkItem | None] | None = None,
    stopped: Callable[[str], float | None] | None = None,
    now: float | None = None,
) -> list[Candidate]:
    """Derive each agent's state from its pane and plate. Reads only — `panes`
    and `plate` are the same readers `crew` and `prime` already use. `plate` may
    be None (no tracker wired) — then every plate reads empty, honestly.

    A RETIRED card reads RETIRED whatever its pane says, and RETIRED is never
    actionable here (#29). Down is the INTENT, not a fault. Alive is a fault — but
    `st tend` is the surface that owns it (it escalates RESURRECTED), and the one
    thing the admin's dispatch list must not do with a retirement is answer it with
    "assign work".

    `stopped` (stopped.py, injected — this module does no I/O) answers "was this
    agent's pane killed BY `st stop`, and when?". A down pane with a record is not a
    defect. It is only consulted for a pane that is actually DOWN: a record for a
    live agent describes a stop that has since been undone, and reading it as
    current would be the launch-stamp mistake — one past fact asserted as a
    standing property. `now` is passed in for the same purity reason.
    """
    out: list[Candidate] = []
    for a in agents:
        item = plate(a.name) if plate else None
        ago = None
        if a.retired:
            state = AgentState.RETIRED
        elif a.pane is None:
            state = AgentState.NO_PANE
        elif not panes.exists(a.pane):
            state = AgentState.STOPPED
            at = stopped(a.name) if stopped else None
            if at is not None:
                state = AgentState.STOPPED_BY_OPERATOR
                if now is not None:
                    ago = max(0.0, now - at)
        elif item is None:
            state = AgentState.IDLE
        else:
            state = AgentState.WORKING
        out.append(Candidate(agent=a.name, role=a.role, state=state, item=item,
                             stopped_ago=ago))
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


def prioritize(candidates: list[Candidate], *,
               stood_down: bool = False) -> PrioritizedWorkflow:
    """Order the actionable candidates. Rule-based baseline (deterministic):
    risen escalations first, then STOPPED, then IDLE; ties by weight desc then
    name. WORKING / NO_PANE / RETIRED / STOPPED_BY_OPERATOR are not actionable right
    now and are omitted — the last of those is REPORTED instead (render), because a
    fleet being deliberately short is worth knowing and is not a defect to fix.

    `stood_down` withholds every DISPATCH step — a stood-down fleet is one the
    operator has decided not to feed, and telling them to feed it anyway is the
    #29 ratchet. Risen escalations still surface: standing the fleet down declines
    to hand out work, it does not decline to ANSWER. What was withheld is reported
    (render), never silently dropped.
    """
    actionable = [c for c in candidates if _tier(c) < _OMIT]
    # Reported, not ranked: a deliberate stop is a fact about the fleet, and a fact
    # does not belong on a list of things to do. `rose` still wins — see _tier.
    deliberate = sorted((c for c in candidates
                         if c.state == AgentState.STOPPED_BY_OPERATOR and not c.rose),
                        key=lambda c: c.agent)
    held: list[Candidate] = []
    if stood_down:
        held = sorted((c for c in actionable if not c.rose), key=lambda c: c.agent)
        actionable = [c for c in actionable if c.rose]
    actionable.sort(key=lambda c: (_tier(c), -c.weight, c.agent))
    steps = [WorkflowStep(rank=i + 1, candidate=c, action=_action(c))
             for i, c in enumerate(actionable)]
    return PrioritizedWorkflow(steps, held=held, stood_down=stood_down,
                               deliberate=deliberate)


_OMIT = 9


def _tier(c: Candidate) -> int:
    if c.rose:
        return 0            # a risen escalation outranks even a retirement: the
                            # agent may be gone, the decision it forced is not
    if c.state == AgentState.STOPPED:
        return 1
    if c.state == AgentState.IDLE:
        return 2
    return _OMIT            # WORKING / NO_PANE / RETIRED / STOPPED_BY_OPERATOR —
                            # nothing to DO about any of them right now


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
    # A step for a deliberately-stopped agent is only reachable via `rose`. The
    # escalation stays the item — this is CONTEXT appended to it, so the admin is
    # not left to wonder why the sender has no pane. It must never be the headline:
    # the stop is not the thing to fix.
    if c.state == AgentState.STOPPED_BY_OPERATOR:
        base += " — stopped by an operator"
        if c.stopped_ago:
            base += f" {_ago(c.stopped_ago)} ago"
    if c.weight > 0:
        base += f" (blast radius {int(c.weight)})"
    return base


def _ago(seconds: float | None) -> str:
    """A coarse age. Minutes and hours only: the difference between 61s and 74s
    does not change a decision, and printing it implies a precision the stamp on
    disk does not have."""
    if not seconds or seconds < 60:
        return "just now"
    mins = int(seconds // 60)
    if mins < 60:
        return f"{mins}m"
    return f"{mins // 60}h{mins % 60:02d}m"
