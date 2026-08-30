"""anchor — what holds an agent to its work. `st anchor`, no arguments.

The most-used surface in any harness: every session starts here (Gas Town's
equivalent ran 21x in the measurement window). Four things, each earning its line
(docs/cli.md):

  1. identity, from the registry — one source, not an env var
  2. the work — ONE item or none. A surface that prints a backlog is a dashboard.
  3. where your stop events go, AND whether that agent will receive them
  4. context + knowledge — both optional; with `none` adapters they vanish

ANCHOR IS A READ. IT MUST NEVER WRITE.

ITEM 3 DESCRIBES A MECHANISM IT DOES NOT OWN, so it must ASK that mechanism
rather than reason about it. anchor probes liveness; the thing an agent actually
wants to know is DELIVERY, and those are different questions with different
answers. Inferring the second from the first is how this section spent its life
telling workers their stop events "go nowhere" while tier.route_stop was rising
every one of them to the administrator exactly as designed — a diagnostic that
under-reports its own system's capability, which cost more than saying nothing
would have: it was repeated to the operator as fact and a working mechanism was
nearly rebuilt (aegis-j1dzp). So the administrator comes from
tier.find_administrator and reachability comes from an injected predicate that
the CLI binds to the router's own. Where they cannot disagree, they cannot drift.

The name is the noun AND the verb (Stiwi, 2026-07-19): an agent's anchor is what
holds it to its work, and `st anchor` is the act of taking hold. It was called
`prime` until then, which named the HARNESS's act (loading a session) rather than
the agent's — and it inherited that name from the tool we left.

Gas Town's primer mutates state from a SessionStart hook, which is why "did I get
primed?" became unanswerable when the hook silently didn't register. Nothing in
this module writes, and test_anchor_writes_nothing asserts it against the
filesystem rather than trusting this docstring — a comment claiming purity is the
thing we keep finding untrue.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .protocols import Agent, Panes, Registry, WorkItem
from .tier import Reason, find_administrator


class Unreachable(Exception):
    """A backend could not be reached. Maps to exit 2 — NOT success, NOT failure.

    cli.md: code 2 exists because we shipped a check that reported CLEAR when it
    could not reach its target. "I could not look" must never render as "fine".
    """


@dataclass
class Anchoring:
    """What anchor found. Rendering is separate so the finding is testable."""
    me: Agent
    item: WorkItem | None
    lead: Agent | None
    lead_up: bool | None          # None = could not tell (no pane to ask about)
    context: list[str]
    knowledge: list[str]
    # Who receives a rise, from tier.find_administrator — the SAME resolution
    # route_stop uses. Section 3 claims where stop events GO, and that claim is
    # only true if it is computed the way the router computes it (aegis-j1dzp).
    # None = this fleet has no administrator, which is the one case where a
    # missing lead really does strand the event.
    admin: str | None = None
    # WHY the lead is unreachable, when the probe could say — the restart-vs-
    # relaunch distinction (tier.LeadStatus). Empty when the probe was a plain
    # bool; never invented, because a fabricated cause on this line is what the
    # tier's own alerts were already burned by.
    lead_detail: str = ""

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
            # BLOCKED WORK MUST SAY SO (aegis-fxx3y). The plate is read as an
            # instruction — "execute immediately" — so serving a dependency-
            # blocked item without a word sends an agent to spend a turn
            # discovering it cannot proceed. Ordering now prefers ready work, so
            # reaching this line means NOTHING ready was on the plate: the item
            # is the best available, and the blocker is the thing to chase.
            blockers = getattr(self.item, "open_blockers", ())
            if blockers:
                L.append(f"      ⛔ BLOCKED by {', '.join(blockers)}"
                         " — nothing ready is on your plate. Chase the blocker,"
                         " or `st ready` for unassigned work.")
        else:
            # Say it plainly. An empty plate is an answer, not a blank section.
            L.append("    nothing. `st go <item> <you>` or ask your lead.")
        L.append("")

        L.append("  YOUR LEAD")
        if self.lead is None:
            # The orphan case is the reason item 3 exists. Do not soften it —
            # but do not overstate it either. route_stop's Q4 sends a lead-less
            # worker's stop STRAIGHT to the administrator (rose=False); it goes
            # nowhere only when there is no administrator to send it to, and
            # there route_stop raises rather than dropping it.
            if self.admin is None:
                L.append("    *** ORPHAN, and this fleet has NO administrator — "
                         "your stop events go NOWHERE. ***")
            elif self.admin == self.me.name:
                L.append("    *** ORPHAN — but you ARE the administrator. "
                         "Your stops terminate here; there is nothing above you. ***")
            else:
                L.append(f"    *** ORPHAN — you report to nobody. Your stop "
                         f"events go DIRECT to {self.admin} (administrator). ***")
        else:
            if self.lead_up is True:
                state = "up. Your stop events go to them."
            elif self.lead_up is False:
                # Say it HERE, not when you stall and discover it — and say what
                # ACTUALLY HAPPENS. This line used to read "your stop events go
                # nowhere right now", which is a claim about DELIVERY inferred
                # from a probe of LIVENESS, and it was false: Q3 rises the event
                # to the administrator with reason `lead-unreachable`, and
                # events.py persists it before anyone reads it (persist =
                # SURVIVAL). A worker read the old line, reported it upward as
                # fact, and a mechanism that already existed and was working was
                # nearly rebuilt (aegis-j1dzp).
                if self.lead.role == "administrator":
                    # Nothing above an administrator to rise TO. route_stop
                    # returns to=lead BEFORE it probes liveness, so the event is
                    # addressed to them and waits on disk for their next drain.
                    state = (f"*** UNREACHABLE. Your stop events are still "
                             f"addressed to {self.lead.name} and persist on disk "
                             f"until they drain — nothing above them to rise to. ***")
                elif self.admin is None:
                    state = ("*** UNREACHABLE, and this fleet has NO administrator "
                             "to rise to — your stop events are STRANDED. ***")
                else:
                    state = (f"*** UNREACHABLE — your stop events RISE TO "
                             f"{self.admin} (administrator, reason: "
                             f"{Reason.LEAD_UNREACHABLE.value}). They persist on "
                             f"disk and are drained at {self.admin}'s next stop — "
                             f"nothing is lost. ***")
            else:
                # Never render "could not tell" as "up". That is the exit-2 bug.
                state = "state UNKNOWN (no pane on the card — could not check)."
            L.append(f"    {self.lead.name} ({self.lead.role}) — {state}")
            # The two causes of `lead-unreachable` want OPPOSITE remedies —
            # restart a DOWN lead, RELAUNCH one that is up but carries no drain
            # hook. tier.LeadStatus carries that distinction; print it when the
            # probe supplied one rather than making the reader go find it.
            if self.lead_detail:
                L.append(f"      why: {self.lead_detail}")

        # Sections 4a/4b vanish entirely when the adapters are `none`. Absent is
        # not the same as empty: an empty heading implies we looked and found
        # nothing, which is a claim we have not earned.
        if self.context:
            L += ["", "  CONTEXT (bobbin)", "    " + " · ".join(self.context)]
        if self.knowledge:
            L += ["", "  KNOWN (quipu)"] + [f"    {k}" for k in self.knowledge]
        return "\n".join(L)


def anchor(
    me: str,
    registry: Registry,
    panes: Panes,
    plate: Callable[[str], WorkItem | None] | None = None,
    context: list[str] | None = None,
    knowledge: list[str] | None = None,
    lead_status: Callable[[str], object] | None = None,
) -> Anchoring:
    """Resolve the four things. Reads only.

    `plate` is INJECTED rather than taken off the Tracker protocol, because
    Tracker is two functions and anchor is not allowed to widen it alone
    (see the note in protocols.Tracker). Pass files.plate bound to a tracker;
    pass None and anchor honestly reports an empty plate rather than
    guessing. Note the tracker is not a parameter at all now: anchor never writes,
    and the only thing it wanted from a tracker was this one read.

    `lead_status` is the reachability predicate, INJECTED for the same reason
    `plate` is. Default: the lead's pane exists. The CLI passes the router's own
    predicate (stop_event._lead_is_up), because REACHABLE MUST MEAN IT WILL
    DRAIN — a pane resurrected by a foreign launcher carries no `drain`
    direction, and anchor answering "up. Your stop events go to them." about a
    lead route_stop treats as unreachable is the same defect as the one this
    parameter was added to fix, pointing the other way. Anything bool-ish is
    accepted; a `.detail` attribute, if present, is carried through to the render
    (tier.LeadStatus supplies one, a plain bool does not).

    Raises LookupError  -> exit 1 (refused: you are not in the registry)
    Raises Unreachable  -> exit 2 (could not tell: a backend was unreachable)
    """
    agent = registry.get(me)                       # 1. identity, one source

    item = plate(me) if plate else None            # 2. one item, or none

    lead: Agent | None = None
    lead_up: bool | None = None
    lead_detail = ""
    # Resolved for BOTH branches: an orphan's stop goes to the administrator too
    # (Q4), so section 3 cannot describe either case without knowing whether one
    # exists. A read of the registry we already hold — no new backend.
    admin = find_administrator(registry)
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
        # ...and whether that agent will RECEIVE. No pane on the card = cannot
        # tell, and cannot-tell must never render as up.
        if lead_status is not None:
            verdict = lead_status(lead.name)
            lead_up = bool(verdict)
            lead_detail = getattr(verdict, "detail", "") or ""
        elif lead.pane:
            lead_up = panes.exists(lead.pane)

    return Anchoring(
        me=agent,
        item=item,
        lead=lead,
        lead_up=lead_up,
        context=list(context or []),
        knowledge=list(knowledge or []),
        admin=admin,
        lead_detail=lead_detail,
    )
