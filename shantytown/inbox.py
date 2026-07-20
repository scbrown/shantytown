"""inbox — messages addressed to an agent. A THIRD type, kept OFF the plate.

Stiwi, 2026-07-19: *"an inbox concept we can map to beads or other ticket
modules."* So the concept is a PROTOCOL with two implementations, selected by the
same `--backend {files,beads}` switch the tracker already uses. There is no second
selection mechanism, and there is no hardcoded store.

THREE TYPES, ONE SUBSTRATE — the argument is events.py's, applied again:

    WORK ITEM   something to DO.        Surfaces on the plate. At most one.
    STOP EVENT  something that HAPPENED to a report. Pushed by a hook, drained
                into a destination's model at its own stop. Never on the plate.
    MESSAGE     something someone SAID to you. Read when you look. Never on the
                plate either.

The rule that matters is the one events.py already paid for: a message must not
become a plate item. plate() returns AT MOST ONE item, so every message that
reached it would EVICT the agent's actual work — one `st inbox ellie "nice job"`
and ellie's plate says "nice job" while a P1 sits behind it. For FilesInbox that
exclusion is STRUCTURAL (its own directory, which no plate reader globs, exactly
like events/). For TrackerInbox it CANNOT be structural — the whole point is that
it lives on the ticket system — so it is a marker the plate readers exclude, and
`is_message()` below is that ONE predicate, shared by both plate readers so they
cannot drift about what a message is.

THE SHAPE (three methods, and the split is the same one events.py makes):

    deliver()    write. The survival guarantee — it is on the store before it is
                 read, so a recipient who is down still gets it.
    unread()     PURE READ. Marks nothing. This is what `st inbox --count` polls,
                 and a counter that consumed what it counted would destroy the
                 delivery it was reporting on (see events.py's RAIL comment; we
                 are not making that mistake twice in one codebase).
    mark_read()  the state change, on its own, explicit. Reading your inbox is an
                 ACT, not a side effect of asking how full it is.

Not four methods: there is no get(id). An inbox you can query by id is a mail
store, and a harness that grows a message store is on its way to being a town
(docs/cli.md, "st inbox is thin, not a bus"). If you need one, that is the
finding — put it on a bead, not in this file.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from .protocols import WorkItem


@dataclass(frozen=True)
class Message:
    """One thing somebody said to an agent. NOT a WorkItem — it never reaches a
    plate. `frm` is optional because a tracker-backed message may not be able to
    carry it (see TrackerInbox): None means "we do not know who sent this", never
    "the system sent it"."""
    id: str
    to: str
    body: str
    frm: str | None = None
    read: bool = False


# The marker a TRACKER-backed message carries, and the ONE place the shape of it
# is written down. A title prefix rather than a label, because it is the only
# field BOTH plate readers can already see: files.plate reads a WorkItem, and a
# WorkItem has no labels. (TrackerInbox also sets a `labels` field so a human can
# `bd list -l inbox`, but the label is convenience — the prefix is the mechanism.
# Two sources of truth for one decision is how they drift.)
PREFIX = "inbox:"

# ...and the prefix `st mail -d` used before the inbox existed. Those items are on
# the live aegis store RIGHT NOW, assigned and open, which means they are on real
# agents' plates today — the exact defect this type exists to prevent. Excluding
# the legacy prefix too is not tidiness; it un-breaks the plates already broken.
_LEGACY_PREFIX = "mail:"


def is_message(title: str) -> bool:
    """Is this tracker item a MESSAGE rather than work? The one predicate, shared
    by files.plate and beads.plate so the two backends cannot disagree about what
    belongs on a plate (the two-implementation equivalence rule, aegis-260i)."""
    t = (title or "").lstrip()
    return t.startswith(PREFIX) or t.startswith(_LEGACY_PREFIX)


def _body_of(title: str) -> str:
    t = (title or "").lstrip()
    for p in (PREFIX, _LEGACY_PREFIX):
        if t.startswith(p):
            return t[len(p):].strip()
    return t


@runtime_checkable
class Inbox(Protocol):
    """Messages addressed to an agent. Three methods — see the module docstring
    for why the read and the mark are separate, and why there is no get(id)."""

    def deliver(self, to: str, body: str, frm: str | None = None) -> Message:
        """WRITE: durably record a message for `to`. It is on the store before it
        is read, so a recipient who is down still receives it."""
        ...

    def unread(self, me: str) -> list[Message]:
        """PURE READ: MY unread messages. Marks NOTHING. Counting is len() of
        this — a count must never consume what it counts."""
        ...

    def mark_read(self, me: str, ids: list[str] | None = None) -> list[Message]:
        """Mark my unread messages read (all of them, or just `ids`) and return
        what was marked. The explicit act; nothing else in the system calls it."""
        ...


class FilesInbox:
    """The zero-dependency floor, and the leak detector for TrackerInbox. One
    message is one json file under <root>/inbox/ — a directory no plate reader
    globs (items live under <root>/items/), so exclusion-from-plate is STRUCTURAL
    here, not a filter that could be forgotten. Same construction as FilesEvents,
    for the same reason."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _next_id(self) -> str:
        if not self.root.is_dir():
            return "msg-1"
        n = 1 + max(
            (int(f.stem[4:]) for f in self.root.glob("msg-*.json") if f.stem[4:].isdigit()),
            default=0,
        )
        return f"msg-{n}"

    def _path(self, msg_id: str) -> Path:
        return self.root / f"{msg_id}.json"

    def _read(self, p: Path) -> Message:
        d = json.loads(p.read_text())
        return Message(id=p.stem, to=d["to"], body=d.get("body", ""),
                       frm=d.get("frm"), read=d.get("read", False))

    def deliver(self, to: str, body: str, frm: str | None = None) -> Message:
        self.root.mkdir(parents=True, exist_ok=True)
        msg = Message(id=self._next_id(), to=to, body=body, frm=frm)
        self._path(msg.id).write_text(json.dumps(
            {"to": msg.to, "frm": msg.frm, "body": msg.body, "read": msg.read},
            indent=2, sort_keys=True))
        return msg

    def unread(self, me: str) -> list[Message]:
        """PURE READ — no mkdir, no rewrite, nothing marked."""
        if not self.root.is_dir():
            return []
        return [m for p in sorted(self.root.glob("msg-*.json"))
                if (m := self._read(p)).to == me and not m.read]

    def mark_read(self, me: str, ids: list[str] | None = None) -> list[Message]:
        marked = []
        for msg in self.unread(me):
            if ids is not None and msg.id not in ids:
                continue
            p = self._path(msg.id)
            d = json.loads(p.read_text())
            d["read"] = True
            p.write_text(json.dumps(d, indent=2, sort_keys=True))
            marked.append(Message(id=msg.id, to=msg.to, body=msg.body,
                                  frm=msg.frm, read=True))
        return marked


class TrackerInbox:
    """The inbox mapped onto a ticket system — beads, or anything else behind the
    Tracker protocol. This is the "map it to beads or other ticket modules" half.

    THE MAPPING, said out loud because every one of these choices is load-bearing:
        a message      -> a tracker item titled `inbox: <body>`, assigned to the
                          recipient. The prefix is what keeps it OFF the plate.
        unread         -> the item is not closed
        mark_read      -> close the item. Reading a message is finishing it; there
                          is nothing else to do with one.
        frm            -> written as the item's description on the way in, and
                          NOT read back: a WorkItem carries id/title/status/
                          assignee and nothing else, so unread() cannot honestly
                          recover a sender. It reports None (= we do not know)
                          rather than a guess.

    LISTING is injected, not taken off the Tracker protocol. That protocol is
    three functions (get/update/create) and pinned by test_swap; "show me the
    items" is a QUERY, and queries are exactly what it excludes to keep the
    tracker from driving the harness (arnold's aegis-gqr8 ruling). The precedent
    is already here: the plate is a per-backend READER injected the same way
    (files.plate / beads.plate). So is this — files.items / beads.items.
    """

    def __init__(self, tracker, items: Callable[[], list[WorkItem]]):
        self._tracker = tracker
        self._items = items

    def deliver(self, to: str, body: str, frm: str | None = None) -> Message:
        fields = {"assignee": to, "labels": "inbox"}
        if frm:
            fields["description"] = f"from {frm}"
        item = self._tracker.create(f"{PREFIX} {body}", **fields)
        return Message(id=item.id, to=to, body=body, frm=frm)

    def unread(self, me: str) -> list[Message]:
        """PURE READ. It lists and filters; it closes nothing."""
        return [
            Message(id=it.id, to=me, body=_body_of(it.title), frm=None)
            for it in self._items()
            if is_message(it.title)
            and it.assignee in (me, me.split("/")[-1])
            and it.status != "closed"
        ]

    def mark_read(self, me: str, ids: list[str] | None = None) -> list[Message]:
        marked = []
        for msg in self.unread(me):
            if ids is not None and msg.id not in ids:
                continue
            self._tracker.update(msg.id, status="closed")
            marked.append(Message(id=msg.id, to=msg.to, body=msg.body,
                                  frm=msg.frm, read=True))
        return marked
