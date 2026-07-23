"""files — the zero-dependency floor.

This module is BOTH the flat registry and the files tracker. It is the second
implementation of each, and its job is to fail loudly if quipu or beads have
leaked into the core. If this is hard to write, the interface is wrong.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

from .inbox import is_message
from .protocols import Agent, WorkItem


def write_json_atomic(path: Path, value) -> None:
    """Write JSON as tmp + rename — a reader sees the old file or the new one,
    never a torn one.

    THE INCIDENT (internal-ref, and events.py's ev-172 before it, same night,
    same disk-full): `write_text()` straight to the final name truncates FIRST.
    A writer killed between the truncate and the write — ENOSPC at 22:37:37 did
    exactly this to notify's blocked.json — leaves a 0-byte file. Every torn
    JSON store then becomes somebody's dam downstream (ev-172 froze sattler's
    drain 47 events deep; she re-slung a closed bead twice off the stale view).
    `os.replace` within one directory is atomic on POSIX.

    events.py carries its own private copy of this pattern (franklin's a12f16e,
    which predates this helper); everything else writes through here.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True))
    os.replace(tmp, path)


class FilesRegistry:
    """Identity from a directory of yaml-ish json. The leak detector for quipu."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def get(self, name: str) -> Agent:
        p = self.root / f"{name}.json"
        if not p.is_file():
            raise LookupError(f"no such agent: {name} (looked in {p})")
        d = json.loads(p.read_text())
        return Agent(
            name=name,
            role=d.get("role", "worker"),
            reports_to=d.get("reports_to"),
            pane=d.get("pane"),
            model=d.get("model"),
            workspace=d.get("workspace"),
            workspace_source=d.get("workspace_source"),
            harness=d.get("harness"),
            dangerous=d.get("dangerous", False),
            retired=d.get("retired", False),
        )

    def set(self, agent: Agent) -> None:
        """Write an agent card. The write half of the registry.

        role set (the generative op in tier.py) is the only thing that should
        call this — it writes the card and emits the routing in one operation so
        the card and the hooks cannot disagree. Preserves fields the tier does
        not own (pane).
        """
        self.root.mkdir(parents=True, exist_ok=True)
        p = self.root / f"{agent.name}.json"
        existing = json.loads(p.read_text()) if p.is_file() else {}
        existing["role"] = agent.role
        existing["reports_to"] = agent.reports_to
        if agent.pane is not None:
            existing["pane"] = agent.pane
        # model, like pane, is a field the tier (role set) does not own — write
        # it only when carried, so a role change preserves the persisted model
        # instead of wiping it (#9).
        if agent.model is not None:
            existing["model"] = agent.model
        # workspace + dangerous are launch config the tier does not own — preserve
        # them across a role change the same way (write only when carried).
        if agent.workspace is not None:
            existing["workspace"] = agent.workspace
        if agent.workspace_source is not None:
            existing["workspace_source"] = agent.workspace_source
        # harness, like model/workspace: launch config the tier does not own, so
        # write it only when carried. A `role set` must never silently move an
        # agent back onto the default harness.
        if agent.harness is not None:
            existing["harness"] = agent.harness
        if agent.dangerous:
            existing["dangerous"] = agent.dangerous
        # retired is written even when False: un-retiring must be expressible,
        # and a field that can only ever be set is a one-way door.
        existing["retired"] = agent.retired
        p.write_text(json.dumps(existing, indent=2, sort_keys=True))

    def all(self) -> list[Agent]:
        """Every agent. RAISES if there is no registry to read.

        This distinction is load-bearing and it was a real bug: glob() on a
        MISSING directory returns [] — no error — so `roles --check` against a
        nonexistent registry reported "0 agents, every one reports somewhere"
        and EXITED 0. That is exactly the defect cli.md says exit code 2 exists
        for: "a check that couldn't reach its target reported CLEAR."

        An empty registry (dir present, no cards) and an absent registry (no dir)
        are DIFFERENT ANSWERS. The first is "nobody exists". The second is "I
        could not look". Only the second is exit 2, and glob() collapses them.
        """
        if not self.root.is_dir():
            raise OSError(f"no registry to read: {self.root} does not exist")
        return [self.get(p.stem) for p in sorted(self.root.glob("*.json"))]


class FilesTracker:
    """A work item is a json file. That's the whole tracker."""

    def __init__(self, root: Path):
        # NO mkdir here. Constructing a tracker must not touch the disk.
        # `st anchor` wires one, and cli.md is explicit: "prime is a read. It
        # must never write." A mkdir in __init__ meant merely ASKING who you are
        # created a directory — a write nobody requested and nobody could see.
        # The mkdir belongs in update(), the only method that writes.
        self.root = Path(root)

    def _path(self, item_id: str) -> Path:
        return self.root / f"{item_id}.json"

    def get(self, item_id: str) -> WorkItem:
        p = self._path(item_id)
        if not p.is_file():
            raise LookupError(f"no such item: {item_id}")
        return self._read(p, item_id)

    def _read(self, p: Path, item_id: str) -> WorkItem:
        d = json.loads(p.read_text())
        return WorkItem(
            id=item_id,
            title=d.get("title", ""),
            status=d.get("status", "open"),
            assignee=d.get("assignee"),
        )

    def update(self, item_id: str, **fields) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        p = self._path(item_id)
        d = json.loads(p.read_text()) if p.is_file() else {}
        d.update({k: v for k, v in fields.items() if v is not None})
        p.write_text(json.dumps(d, indent=2, sort_keys=True))


    def create(self, title: str, **fields) -> WorkItem:
        """New item. Returns it, because the caller needs the id it did not have.

        The id is content-free and monotonic: creation must not need a server.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        n = 1 + max(
            (int(f.stem[3:]) for f in self.root.glob("st-*.json") if f.stem[3:].isdigit()),
            default=0,
        )
        item_id = f"st-{n}"
        d = {"title": title, "status": "open"}
        d.update({k: v for k, v in fields.items() if v is not None})
        self._path(item_id).write_text(json.dumps(d, indent=2, sort_keys=True))
        return WorkItem(id=item_id, title=title, status="open", assignee=d.get("assignee"))


# Plate precedence, identical to beads._PLATE_RANK so both backends order a plate
# the same way (two-implementation equivalence).
_PLATE_RANK = {"hooked": 0, "in_progress": 1}


def plate(tracker: FilesTracker, agent: str) -> WorkItem | None:
    """The ONE thing on an agent's plate, or None. A module function, not a method.

    WHY IT LIVES HERE AND NOT ON THE PROTOCOL: `st anchor` must
    answer "what's on my plate" and Tracker cannot — get() needs an id you do not
    have yet. I first solved that by adding a third method, mine(), to Tracker.
    That broke test_swap's two-function assertion AND the BeadsTracker isinstance
    check — ellie's test caught a shared-contract change I had no business making
    alone. It was right; this is the holding position until arnold rules.

    Reading `tracker.root` here is not a leak: this module OWNS the files layout.
    It is the files adapter answering a question about its own storage. It IS a
    debt — every new tracker now owes a plate reader the protocol does not
    describe, and beads.py has none, so prime against beads shows an empty plate
    rather than a wrong one.

    Returns AT MOST ONE item, by construction. cli.md: "One item, or none. A
    primer that prints a backlog is a dashboard." Ties broken by id so two runs
    agree — a primer that reorders itself is a primer nobody trusts.
    """
    root = Path(tracker.root)
    if not root.is_dir():
        return None
    mine = [
        item
        for p in sorted(root.glob("*.json"))
        if (item := tracker._read(p, p.stem)).assignee == agent
        and item.status != "closed"
        # A MESSAGE is not work (inbox.py). The plate holds at most one item, so
        # a message that reached it would EVICT the agent's actual work. Shared
        # predicate with beads.plate — one judgment, both backends.
        and not is_message(item.title)
    ]
    if not mine:
        return None
    # Shared precedence with beads.plate: in-hand outranks
    # not-started, then id. Was pure filename order, which would surface an
    # unstarted open item over one you're mid-flight on — the wrong plate.
    mine.sort(key=lambda it: (_PLATE_RANK.get(it.status, 2), it.id))
    return mine[0]


def items(tracker: FilesTracker) -> list[WorkItem]:
    """EVERY item in this store. The per-backend LISTER, injected into
    TrackerInbox exactly the way plate() is injected into anchor — a query, and
    queries stay off the three-function Tracker protocol (internal-ref).

    Returns [] for an absent store, which is correct HERE and would not be in
    all(): a tracker directory that does not exist yet is a store with no items,
    and update()/create() will make it on the first write. (Contrast
    FilesRegistry.all, which RAISES — you cannot have an agent whose identity you
    cannot read, but you can absolutely have an empty tracker.)
    """
    root = Path(tracker.root)
    if not root.is_dir():
        return []
    return [tracker._read(p, p.stem) for p in sorted(root.glob("*.json"))]
