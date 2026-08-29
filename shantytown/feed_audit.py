"""Durable evidence for the natural haul-feed path (aegis-mxgzh.1).

One tend pass is a window.  Every row carries the same explicit booleans so an
operator can distinguish "eligible but never attempted" from "attempted and
refused" and from a delivery that actually landed.  The file is append-only:
the audit must survive the supervisor process that produced it.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


class FeedAudit:
    def __init__(self, root: Path, *, window_id=None):
        self.path = Path(root) / "logs" / "feed-audit.jsonl"
        self._window_id = window_id

    def begin(self) -> str:
        if self._window_id is not None:
            return str(self._window_id()) if callable(self._window_id) else str(self._window_id)
        return f"{time.time_ns()}-{uuid.uuid4().hex[:8]}"

    def record(self, window_id: str, *, leg: str, backend: str = "",
               worker: str = "", item: str = "", eligible: bool = False,
               attempted: bool = False, acted_on: bool = False,
               refused: bool = False, reason: str = "") -> None:
        row = {
            "at": time.time(), "window_id": window_id, "leg": leg,
            "backend": backend, "worker": worker, "item": item,
            "eligible": bool(eligible), "attempted": bool(attempted),
            "acted_on": bool(acted_on), "refused": bool(refused),
            "reason": reason,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(row, sort_keys=True) + "\n").encode()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)

    def acted_on(self, window_id: str, worker: str, item: str) -> bool:
        """True only for a matching landed delivery; malformed rows prove nothing."""
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if (row.get("window_id") == window_id
                        and row.get("worker") == worker
                        and row.get("item") == item
                        and row.get("acted_on") is True):
                    return True
        except OSError:
            return False
        return False
