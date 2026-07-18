"""events — the stop-event stream. A TYPED substrate, kept OFF the plate.

arnold's #6 ruling (gt-wisp-w4j2af). A stop event is NOT a work item:

    plate() surfaces tracker items assigned to the agent — so if stop-events were
    plain items, every worker idle-stop would become the lead's "one plate item",
    crowding out real work (and the plate is singular — it cannot hold a flood).
    So stop-events are a DISTINCT TYPE; the drain reads them by that type; plate()
    EXCLUDES them. A stop event is not work you pick up; it is an event the hook
    pushes.

Two halves meet HERE, in this store (arnold's correction to my proposal):
    SEND  — a non-root role, at its own stop, PERSISTS an event addressed to its
            route_stop destination. persist = SURVIVAL: a rise to an absent admin
            cannot vanish, because it is on disk before anyone reads it.
    RECEIVE — a destination (lead/admin), at ITS stop, DRAINS the events addressed
            to it and injects them into its MODEL (decision:block + reason). The
            blocking hook draining this store is DELIVERY. That is why lead/admin
            need blocking_stop and a worker does not — it never receives.

persist is survival; drain is delivery. They are not either/or; they are the two
ends of one durable seam.

RAIL — BLOCK-ONCE (mandatory, the single most likely way to ship a wedged tier).
Claude Code Stop hooks fire on EVERY stop. A drain that re-blocks whenever the
store is non-empty loops the destination forever — it can never go idle. So drain
MARKS each event delivered and returns it ONCE; a later stop with nothing new
drains empty and the destination idles.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class StopEvent:
    """One agent stopped; this is where its stop went. NOT a WorkItem — it never
    reaches a plate. `reason` is None for a clean finish; route_stop fills it
    (LEAD_UNREACHABLE) on a rise, and a lead fills it on escalate."""
    id: str
    to: str                      # the destination route_stop chose
    frm: str                     # who stopped
    reason: str | None = None
    rose: bool = False           # did it rise past a down lead to the admin?
    delivered: bool = False      # BLOCK-ONCE marker: has the drain handed it over?


@runtime_checkable
class Events(Protocol):
    """The stop-event stream. Two methods, and neither is get/update/create — this
    is deliberately NOT the Tracker (whose three-method surface is pinned, aegis-
    gqr8). Sharing the Tracker's SUBSTRATE (the aegis store) does not mean sharing
    its protocol; a stop event and a work item are different types on one store."""
    def persist(self, to: str, frm: str, reason: str | None, rose: bool) -> StopEvent:
        """SEND: durably record an event addressed to `to`. Survival guarantee —
        it is on the store before it is read, so it cannot vanish if `to` is down."""
        ...
    def drain(self, me: str) -> list[StopEvent]:
        """RECEIVE: return MY undelivered events and MARK them delivered (block-
        once). A second drain with nothing new returns [] — the destination idles."""
        ...


class FilesEvents:
    """The zero-dependency floor, and the leak detector for BeadsEvents. One event
    is one json file under <root>/events/ — a directory the tracker's plate() never
    globs (items live under <root>/items/), so exclusion-from-plate is STRUCTURAL
    here, not a filter that could be forgotten."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _next_id(self) -> str:
        if not self.root.is_dir():
            return "ev-1"
        n = 1 + max(
            (int(f.stem[3:]) for f in self.root.glob("ev-*.json") if f.stem[3:].isdigit()),
            default=0,
        )
        return f"ev-{n}"

    def persist(self, to: str, frm: str, reason: str | None, rose: bool) -> StopEvent:
        self.root.mkdir(parents=True, exist_ok=True)
        ev = StopEvent(id=self._next_id(), to=to, frm=frm, reason=reason, rose=rose)
        (self.root / f"{ev.id}.json").write_text(json.dumps({
            "to": ev.to, "frm": ev.frm, "reason": ev.reason,
            "rose": ev.rose, "delivered": ev.delivered,
        }, indent=2, sort_keys=True))
        return ev

    def _read(self, p: Path) -> StopEvent:
        d = json.loads(p.read_text())
        return StopEvent(id=p.stem, to=d["to"], frm=d["frm"], reason=d.get("reason"),
                         rose=d.get("rose", False), delivered=d.get("delivered", False))

    def drain(self, me: str) -> list[StopEvent]:
        if not self.root.is_dir():
            return []
        mine = []
        for p in sorted(self.root.glob("ev-*.json")):
            ev = self._read(p)
            if ev.to == me and not ev.delivered:
                mine.append(ev)
                # BLOCK-ONCE: mark delivered NOW, so the next stop drains empty and
                # the destination can idle instead of re-blocking every turn.
                d = json.loads(p.read_text())
                d["delivered"] = True
                p.write_text(json.dumps(d, indent=2, sort_keys=True))
        return mine


class NullEvents:
    """Second implementation — the leak detector. In-memory, so it proves the
    SEND/RECEIVE logic never reaches for the disk or bd. Tests use it."""

    def __init__(self):
        self._events: list[StopEvent] = []
        self._n = 0

    def persist(self, to: str, frm: str, reason: str | None, rose: bool) -> StopEvent:
        self._n += 1
        ev = StopEvent(id=f"ev-{self._n}", to=to, frm=frm, reason=reason, rose=rose)
        self._events.append(ev)
        return ev

    def drain(self, me: str) -> list[StopEvent]:
        mine = [e for e in self._events if e.to == me and not e.delivered]
        # replace with delivered=True copies (frozen dataclass) — block-once.
        for e in mine:
            self._events[self._events.index(e)] = StopEvent(
                id=e.id, to=e.to, frm=e.frm, reason=e.reason, rose=e.rose, delivered=True)
        return mine
