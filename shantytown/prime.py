"""prime — the primer. `shanty prime`, no arguments.

The most-used surface in any harness: every session starts here (Gas Town's ran
21x in the measurement window). Four things, each earning its line (docs/cli.md):

  1. identity, from the registry — one source, not an env var
  2. the work — ONE item or none. A primer that prints a backlog is a dashboard.
  3. where your stop events go, AND whether that agent is up
  4. context + knowledge — both optional; with `none` adapters they vanish

PRIME IS A READ. IT MUST NEVER WRITE.

Gas Town's primer mutates state from a SessionStart hook, which is why "did I
get primed?" became unanswerable when the hook silently didn't register. Nothing
in this module writes, and test_prime_writes_nothing asserts it against the
filesystem rather than trusting this docstring — a comment claiming purity is
the thing we keep finding untrue.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .protocols import Agent, Panes, Registry, WorkItem


class Unreachable(Exception):
    """A backend could not be reached. Maps to exit 2 — NOT success, NOT failure.

    cli.md: code 2 exists because we shipped a check that reported CLEAR when it
    could not reach its target. "I could not look" must never render as "fine".
    """


@dataclass
class Priming:
    """What prime found. Rendering is separate so the finding is testable."""
    me: Agent
    item: WorkItem | None
    lead: Agent | None
    lead_up: bool | None          # None = could not tell (no pane to ask about)
    context: list[str]
    knowledge: list[str]

    def render(self) -> str:
        L: list[str] = []
        who = f"  You are {self.me.name} — {self.me.role}"
        if self.me.reports_to:
            who += f", reports to {self.me.reports_to}"
        L += [who + ".", ""]

        L.append("  ON YOUR PLATE")
        if self.item:
            L.append(f"    ▶ {self.item.id}  {self.item.title}".rstrip()
                     + f"        ({self.item.status})")
        else:
            # Say it plainly. An empty plate is an answer, not a blank section.
            L.append("    nothing. `shanty go <item> <you>` or ask your lead.")
        L.append("")

        L.append("  YOUR LEAD")
        if self.lead is None:
            # The orphan case is the reason item 3 exists. Do not soften it.
            L.append("    *** ORPHAN — you report to nobody. "
                     "Your stop events go NOWHERE. ***")
        else:
            if self.lead_up is True:
                state = "up. Your stop events go to them."
            elif self.lead_up is False:
                # Say it HERE, not when you stall and discover it.
                state = "*** DOWN — your stop events go nowhere right now. ***"
            else:
                # Never render "could not tell" as "up". That is the exit-2 bug.
                state = "state UNKNOWN (no pane on the card — could not check)."
            L.append(f"    {self.lead.name} ({self.lead.role}) — {state}")

        # Sections 4a/4b vanish entirely when the adapters are `none`. Absent is
        # not the same as empty: an empty heading implies we looked and found
        # nothing, which is a claim we have not earned.
        if self.context:
            L += ["", "  CONTEXT (bobbin)", "    " + " · ".join(self.context)]
        if self.knowledge:
            L += ["", "  KNOWN (quipu)"] + [f"    {k}" for k in self.knowledge]
        return "\n".join(L)


def prime(
    me: str,
    registry: Registry,
    panes: Panes,
    plate: Callable[[str], WorkItem | None] | None = None,
    context: list[str] | None = None,
    knowledge: list[str] | None = None,
) -> Priming:
    """Resolve the four things. Reads only.

    `plate` is INJECTED rather than taken off the Tracker protocol, because
    Tracker is two functions and prime is not allowed to widen it alone
    (aegis-gqr8 — see the note in protocols.Tracker). Pass files.plate bound to
    a tracker; pass None and prime honestly reports an empty plate rather than
    guessing. Note the tracker is not a parameter at all now: prime never writes,
    and the only thing it wanted from a tracker was this one read.

    Raises LookupError  -> exit 1 (refused: you are not in the registry)
    Raises Unreachable  -> exit 2 (could not tell: a backend was unreachable)
    """
    agent = registry.get(me)                       # 1. identity, one source

    item = plate(me) if plate else None            # 2. one item, or none

    lead: Agent | None = None
    lead_up: bool | None = None
    if agent.reports_to:                           # 3. where stop events go
        try:
            lead = registry.get(agent.reports_to)
        except LookupError:
            # The card names a lead who is not in the registry. That is a broken
            # card, not an orphan, and it is a precondition failure — refuse.
            raise LookupError(
                f"{me}'s card says reports_to={agent.reports_to!r}, "
                f"but no such agent is in the registry"
            )
        # ...and whether that agent is UP. No pane on the card = cannot tell.
        lead_up = panes.exists(lead.pane) if lead.pane else None

    return Priming(
        me=agent,
        item=item,
        lead=lead,
        lead_up=lead_up,
        context=list(context or []),
        knowledge=list(knowledge or []),
    )
