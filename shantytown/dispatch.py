"""dispatch — `shanty go <item> [agent]`.

The command this repo exists for. gt sling takes >120s; its --dry-run alone
takes 51s and writes nothing, because the cost is 63 sequential Dolt
connections during RESOLUTION, before any write (aegis-eu3s). Underneath,
dispatch is tmux send-keys.

This module does: one registry read, one tracker read, one tracker write,
one send. That is the budget, and it is asserted in the tests.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field

from .protocols import Panes, Registry, Tracker
from .triage import Action, Decision, triage

# Verify reads SCROLLBACK and polls briefly. Measured against a real Claude Code
# agent (harding, first live dispatches): a visible-pane one-shot check NEVER
# confirmed a delivery that plainly worked — the agent consumes the input line on
# submit and its own output scrolls the echoed id off the visible 24 lines before
# we look. So `st go` always reported could-not-tell and NEVER recorded the
# tracker update. The failure direction was safe, but the check was structurally
# incapable of SUCCEEDING against the runtime we actually use.
_VERIFY_HISTORY = 200
_VERIFY_ATTEMPTS = 5
_VERIFY_DELAY = 0.3


class TriageRefused(Exception):
    """`st go` declined to send because the target pane is not ready to receive.

    Carries the whole Decision so the caller can print WHY and on what inputs —
    a refusal you cannot inspect is indistinguishable from a bug. Maps to exit 1.
    """

    def __init__(self, decision: Decision):
        self.decision = decision
        super().__init__(decision.why)


class SendUnverified(Exception):
    """We sent, but reading the pane back did NOT show the work (#2).

    Maps to exit 2 (could-not-confirm), NOT exit 0. The critical consequence is
    in go(): because verify runs BEFORE the tracker write, an unverified send
    leaves the item UNTOUCHED — never marked in_progress for a send that may not
    have landed. "Send-and-assume is how you believe work was assigned when it
    wasn't" (design.md). The honest failure is "I could not confirm delivery, so
    I recorded nothing" — a human re-dispatches, rather than a tracker full of
    items nobody was told about.
    """

    def __init__(self, item_id: str, pane: str):
        self.item_id, self.pane = item_id, pane
        super().__init__(f"sent {item_id} to {pane} but could not confirm it landed")


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
        # #2: SEND -> VERIFY -> UPDATE, in that order, on purpose. The tracker
        # write moved AFTER a confirmed send so a dropped send never marks work
        # in_progress. verify reads the pane back for the item id — the thing we
        # just sent must now be visible on the pane. If it is not, we sent into
        # the void: raise SendUnverified (exit 2) and write NOTHING.
        self.panes.send(p.pane, p.text)                # 1 send
        if not self.verify(p.pane, item_id):
            raise SendUnverified(item_id, p.pane)
        self.tracker.update(item_id, **p.updates)      # 1 tracker write (last)
        return p

    def verify(self, pane: str, item_id: str) -> bool:
        """Did the send land? Read the pane back and look for the item id.

        design.md: "verify reads the pane back. Send-and-assume is how you
        believe work was assigned when it wasn't." A false negative (the agent
        cleared it before we looked) is SAFE by construction: it maps to exit 2
        and leaves the tracker untouched, so a human re-dispatches rather than
        the tracker lying — never the other direction.

        But safe-by-construction is not an excuse for a check that can only ever
        fail. Reading the VISIBLE pane once never confirmed a real delivery to a
        Claude Code agent (see the constants above), so this reads SCROLLBACK and
        polls: the echoed id survives in history even after the agent's own
        output pushes it off-screen. Still one-directional — we only ever return
        True on positive evidence that the id reached the pane.
        """
        for attempt in range(_VERIFY_ATTEMPTS):
            if item_id in self.panes.capture(pane, history=_VERIFY_HISTORY):
                return True
            if attempt + 1 < _VERIFY_ATTEMPTS:
                time.sleep(_VERIFY_DELAY)
        return False
