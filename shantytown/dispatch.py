"""dispatch — `shanty go <item> [agent]`.

The command this repo exists for. gt sling takes >120s; its --dry-run alone
takes 51s and writes nothing, because the cost is 63 sequential Dolt
connections during RESOLUTION, before any write (aegis-eu3s). Underneath,
dispatch is tmux send-keys.

This module does: one registry read, one tracker read, one tracker write,
one send. That is the budget, and it is asserted in the tests.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from .protocols import Panes, Registry, Tracker
from .triage import Action, Decision, triage


class TriageRefused(Exception):
    """`st go` declined to send because the target pane is not ready to receive.

    Carries the whole Decision so the caller can print WHY and on what inputs —
    a refusal you cannot inspect is indistinguishable from a bug. Maps to exit 1.
    """

    def __init__(self, decision: Decision):
        self.decision = decision
        super().__init__(decision.why)


@dataclass
class Plan:
    """What a dispatch WOULD do. --dry-run returns this and stops."""
    item_id: str
    agent: str
    pane: str
    updates: dict = field(default_factory=dict)
    text: str = ""

    def render(self) -> str:
        return "\n".join([
            f"  would: tracker.update({self.item_id}, "
            + ", ".join(f"{k}={v}" for k, v in self.updates.items())
            + ")",
            f"  would: send-keys -> pane {self.pane}",
            "  would NOT: create a convoy, spawn a session, wait for ack",
        ])


class Dispatcher:
    def __init__(self, registry: Registry, tracker: Tracker, panes: Panes):
        self.registry = registry
        self.tracker = tracker
        self.panes = panes

    def plan(self, item_id: str, agent_name: str) -> Plan:
        """Resolve only. No writes. This is what --dry-run shows.

        Every refusal here is a precondition failure -> exit 1, and it happens
        BEFORE anything is written. Refusing loudly beats a half-dispatch.
        """
        agent = self.registry.get(agent_name)          # 1 registry read
        if agent.pane is None:
            raise LookupError(f"{agent_name} has no pane in the registry")
        if not self.panes.exists(agent.pane):
            raise LookupError(f"pane {agent.pane} for {agent_name} does not exist")
        item = self.tracker.get(item_id)               # 1 tracker read
        return Plan(
            item_id=item_id,
            agent=agent_name,
            pane=agent.pane,
            updates={"status": "in_progress", "assignee": agent_name},
            text=f"Work is on your hook: {item_id} — {item.title}",
        )

    def triage(self, item_id: str, agent_name: str) -> Decision:
        """What st go WOULD do to that pane, without touching it. Read-only.

        Closes shantytown #1: st go sent into mid-flight panes. It went straight
        to send-keys, so dispatching to an agent that was mid-response
        interrupted its work. Now go() consults sentinel's triage first and only
        NUDGE proceeds. This method exposes that judgement for --dry-run and for
        `st go` to print before it refuses.
        """
        p = self.plan(item_id, agent_name)       # resolve + precondition-check
        return triage(self.panes, p.pane, p.text)

    def go(self, item_id: str, agent_name: str, dry_run: bool = False) -> Plan:
        p = self.plan(item_id, agent_name)
        if dry_run:
            return p
        # #1: consult triage BEFORE any write. A REFUSE/CLEAR/RESTART here means
        # we never mark the item in_progress and never send — no half-dispatch,
        # no interrupted agent. Only a healthy pane (NUDGE) proceeds.
        decision = triage(self.panes, p.pane, p.text)
        if decision.action is not Action.NUDGE:
            raise TriageRefused(decision)
        self.tracker.update(item_id, **p.updates)      # 1 tracker write
        self.panes.send(p.pane, p.text)                # 1 send
        return p
