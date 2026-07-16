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
        return [self.get(p.stem) for p in sorted(self.root.glob("*.json"))]


class FilesTracker:
    """A work item is a json file. That's the whole tracker."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, item_id: str) -> Path:
        return self.root / f"{item_id}.json"

    def get(self, item_id: str) -> WorkItem:
        p = self._path(item_id)
        if not p.is_file():
            raise LookupError(f"no such item: {item_id}")
        d = json.loads(p.read_text())
        return WorkItem(
            id=item_id,
            title=d.get("title", ""),
            status=d.get("status", "open"),
            assignee=d.get("assignee"),
        )

    def update(self, item_id: str, **fields) -> None:
        p = self._path(item_id)
        d = json.loads(p.read_text()) if p.is_file() else {}
        d.update({k: v for k, v in fields.items() if v is not None})
        p.write_text(json.dumps(d, indent=2, sort_keys=True))
