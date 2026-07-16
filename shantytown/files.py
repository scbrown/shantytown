"""files — the zero-dependency floor.

This module is BOTH the flat registry and the files tracker. It is the second
implementation of each, and its job is to fail loudly if quipu or beads have
leaked into the core. If this is hard to write, the interface is wrong.
"""
from __future__ import annotations
import json
from pathlib import Path

from .protocols import Agent, WorkItem


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
        )

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
        # `shanty prime` wires one, and cli.md is explicit: "prime is a read. It
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


def plate(tracker: FilesTracker, agent: str) -> WorkItem | None:
    """The ONE thing on an agent's plate, or None. A module function, not a method.

    WHY IT LIVES HERE AND NOT ON THE PROTOCOL (aegis-gqr8): `shanty prime` must
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
    for p in sorted(root.glob("*.json")):
        item = tracker._read(p, p.stem)
        if item.assignee == agent and item.status != "closed":
            return item
    return None
