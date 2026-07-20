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

WHAT AN EVENT MUST CARRY (aegis-w9z1, measured by sattler 2026-07-19). The rail
above names the fact that makes the naive payload unactionable: a Stop hook fires
per TURN, not per SESSION. So "X stopped" does NOT mean X is idle — sattler was
handed three such events, opened both panes, and found tim in `Envisioning… (39s)`
and kelly in `Musing… (38s)`, both mid-flight, both items still in_progress. Acting
on the event name would have re-dispatched over two working agents — the exact
mid-flight send dispatch/triage exists to refuse. And the payload was
{delivered, frm, reason, rose}: NO timestamp (events cannot be ordered or aged),
NO item (the coordinator must go re-read the tracker per agent), and `reason` is
the ROUTING reason, null in every real event. The event could not be acted on
without redoing by hand the whole investigation it was supposed to save.

So an event now records `ts` (when) and `item`/`item_status` (what it held, and
whether that moved). It deliberately does NOT record a liveness verdict: the
sender is INSIDE its own Stop hook when it persists, so the only pane it could
judge is its own, mid-hook — and any verdict stamped at emit is stale by the time
a destination reads it, which is precisely the failure being fixed. Liveness is
therefore computed at DRAIN time, by the reader, against a live pane
(stop_event._drain). Here we store only facts that are true when written.
"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable


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
    shells: int | None = None    # background shells the sender still owned AT STOP
                                 # (aegis-q73g). "weaver stopped" and "weaver
                                 # stopped, 1 shell still running" are different
                                 # facts and only the second is actionable — a
                                 # stop event carrying only the first invites the
                                 # destination to book turn-end as task-end.
                                 # None = NOT REPORTED, never "zero".
    ts: float = 0.0              # epoch seconds at persist. 0.0 = UNSTAMPED (an
                                 # event written before aegis-w9z1) — the reader
                                 # must render that as "age unknown", never as
                                 # "just now", which is the one wrong answer.
    item: str | None = None      # what `frm` held at its stop, if anything
    item_status: str | None = None
                                 # its status, or "?" meaning COULD NOT LOOK.
                                 # item=None + status=None is "plate was empty";
                                 # item=None + status="?" is "the tracker did not
                                 # answer". Collapsing those two would let a
                                 # coordinator read a failed lookup as finished
                                 # work — the aegis-mt0r class.


@runtime_checkable
class Events(Protocol):
    """The stop-event stream. Two methods, and neither is get/update/create — this
    is deliberately NOT the Tracker (whose three-method surface is pinned, aegis-
    gqr8). Sharing the Tracker's SUBSTRATE (the aegis store) does not mean sharing
    its protocol; a stop event and a work item are different types on one store."""
    def persist(self, to: str, frm: str, reason: str | None, rose: bool,
                shells: int | None = None, item: str | None = None,
                item_status: str | None = None) -> StopEvent:
        """SEND: durably record an event addressed to `to`. Survival guarantee —
        it is on the store before it is read, so it cannot vanish if `to` is down.

        Everything past `rose` is optional so an Events impl written before q73g /
        w9z1 still satisfies this protocol — the fields are additive, and a caller
        that cannot measure one passes nothing and gets None (not reported), which
        is the truth."""
        ...
    def drain(self, me: str, accept: Callable[[StopEvent], bool] | None = None
              ) -> list[StopEvent]:
        """RECEIVE: return MY undelivered events and MARK them delivered (block-
        once). A second drain with nothing new returns [] — the destination idles.

        `accept` DEFERS rather than filters: an event it rejects is neither
        returned NOR marked, so it stays pending for a later drain. That is how a
        reader declines to be woken by a turn boundary (the sender is still
        mid-flight) without ever dropping the event. Rejecting everything is safe:
        drain returns [] and the reader idles, exactly as with an empty store.

        This is a SIGNATURE widening, not a surface one: still two methods, and
        the predicate stays with the CALLER, so the store never learns what
        'busy' means (aegis-w9z1)."""
        ...


class FilesEvents:
    """The zero-dependency floor, and the leak detector for BeadsEvents. One event
    is one json file under <root>/events/ — a directory the tracker's plate() never
    globs (items live under <root>/items/), so exclusion-from-plate is STRUCTURAL
    here, not a filter that could be forgotten."""

    def __init__(self, root: Path):
        self.root = Path(root)

    @staticmethod
    def _n(stem: str) -> int:
        """ev-10 sorts BEFORE ev-2 as a string. Order events by their number, so
        'oldest first' means what it says — the reader now ages and collapses
        them, and a lexicographic order would hand it the wrong 'latest'."""
        tail = stem[3:]
        return int(tail) if tail.isdigit() else 0

    def _next_id(self) -> str:
        if not self.root.is_dir():
            return "ev-1"
        n = 1 + max(
            (int(f.stem[3:]) for f in self.root.glob("ev-*.json") if f.stem[3:].isdigit()),
            default=0,
        )
        return f"ev-{n}"

    def persist(self, to: str, frm: str, reason: str | None, rose: bool,
                shells: int | None = None, item: str | None = None,
                item_status: str | None = None) -> StopEvent:
        self.root.mkdir(parents=True, exist_ok=True)
        ev = StopEvent(id=self._next_id(), to=to, frm=frm, reason=reason, rose=rose,
                       shells=shells, ts=time.time(), item=item,
                       item_status=item_status)
        (self.root / f"{ev.id}.json").write_text(json.dumps({
            "to": ev.to, "frm": ev.frm, "reason": ev.reason,
            "rose": ev.rose, "delivered": ev.delivered, "shells": ev.shells,
            "ts": ev.ts, "item": ev.item, "item_status": ev.item_status,
        }, indent=2, sort_keys=True))
        return ev

    def _read(self, p: Path) -> StopEvent:
        d = json.loads(p.read_text())
        # shells defaults to None for events written before q73g — an old event
        # genuinely did not report one, so the default IS the correct reading.
        return StopEvent(id=p.stem, to=d["to"], frm=d["frm"], reason=d.get("reason"),
                         rose=d.get("rose", False), delivered=d.get("delivered", False),
                         shells=d.get("shells"),
                         ts=float(d.get("ts") or 0.0), item=d.get("item"),
                         item_status=d.get("item_status"))

    def drain(self, me: str, accept=None) -> list[StopEvent]:
        if not self.root.is_dir():
            return []
        mine = []
        for p in sorted(self.root.glob("ev-*.json"), key=lambda q: self._n(q.stem)):
            ev = self._read(p)
            if ev.to != me or ev.delivered:
                continue
            if accept is not None and not accept(ev):
                continue          # DEFERRED: not returned, and NOT marked — it
                                  # stays pending for a later drain.
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

    def persist(self, to: str, frm: str, reason: str | None, rose: bool,
                shells: int | None = None, item: str | None = None,
                item_status: str | None = None) -> StopEvent:
        self._n += 1
        ev = StopEvent(id=f"ev-{self._n}", to=to, frm=frm, reason=reason, rose=rose,
                       shells=shells, ts=time.time(), item=item,
                       item_status=item_status)
        self._events.append(ev)
        return ev

    def drain(self, me: str, accept=None) -> list[StopEvent]:
        mine = [e for e in self._events
                if e.to == me and not e.delivered
                and (accept is None or accept(e))]     # rejected -> stays pending
        # replace with delivered=True copies (frozen dataclass) — block-once.
        for e in mine:
            self._events[self._events.index(e)] = StopEvent(
                id=e.id, to=e.to, frm=e.frm, reason=e.reason, rose=e.rose,
                delivered=True, shells=e.shells, ts=e.ts, item=e.item,
                item_status=e.item_status)
        return mine
