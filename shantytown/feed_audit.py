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


START_UNACKNOWLEDGED = "START_UNACKNOWLEDGED"


def codex_turn_starts(session_root: Path, *, max_age_s: float = 3600,
                      tail_bytes: int = 1_000_000) -> set[tuple[str, str]]:
    """Return (serve_id, worker) receipts backed by Codex task_started events.

    The marker alone is not a receipt: the same turn must have Codex's own
    task_started event and a UserMessage carrying the marker.
    """
    found: set[tuple[str, str]] = set()
    for path in Path(session_root).glob("**/*.jsonl"):
        turns: set[str] = set()
        messages: list[tuple[str, str]] = []
        try:
            if time.time() - path.stat().st_mtime > max_age_s:
                continue
            with path.open("rb") as f:
                f.seek(max(0, path.stat().st_size - tail_bytes))
                data = f.read().decode("utf-8", errors="ignore")
            lines = data.splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            payload = row.get("payload") or {}
            if row.get("type") == "event_msg" and payload.get("type") == "task_started":
                if payload.get("turn_id"):
                    turns.add(payload["turn_id"])
            item = payload.get("item") or {}
            if (row.get("type") == "event_msg"
                    and payload.get("type") == "item_completed"
                    and item.get("type") == "UserMessage"):
                text = "\n".join(str(c.get("text", "")) for c in item.get("content", []))
                messages.append((payload.get("turn_id", ""), text))
        for turn_id, text in messages:
            if turn_id not in turns:
                continue
            import re
            for serve_id, worker in re.findall(
                    r"\[st serve:([0-9A-Za-z_.-]+) worker:([0-9A-Za-z_.-]+)\]", text):
                found.add((serve_id, worker))
    return found


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
               refused: bool = False, reason: str = "", serve_id: str = "",
               state: str = "") -> None:
        row = {
            "at": time.time(), "window_id": window_id, "leg": leg,
            "backend": backend, "worker": worker, "item": item,
            "eligible": bool(eligible), "attempted": bool(attempted),
            "acted_on": bool(acted_on), "refused": bool(refused),
            "reason": reason,
            "serve_id": serve_id, "state": state,
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

    def new_serve(self) -> str:
        return uuid.uuid4().hex

    def reconcile_turn_starts(self, receipts: set[tuple[str, str]], *, now=None,
                              timeout_s: float = 30.0) -> list[str]:
        """Advance input_sent serves from Codex evidence; name timed-out serves."""
        now = time.time() if now is None else now
        latest: dict[str, dict] = {}
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if row.get("serve_id"):
                    latest[row["serve_id"]] = row
        except OSError:
            return []
        unacked = []
        for serve_id, row in latest.items():
            state = row.get("state")
            worker = row.get("worker", "")
            if state == "input_sent" and (serve_id, worker) in receipts:
                self.record(row.get("window_id", ""), leg="receipt",
                            backend=row.get("backend", ""), worker=worker,
                            item=row.get("item", ""), acted_on=True,
                            serve_id=serve_id, state="turn_started",
                            reason="Codex task_started + matching UserMessage marker")
            elif state == "input_sent" and now - float(row.get("at", now)) >= timeout_s:
                self.record(row.get("window_id", ""), leg="receipt",
                            backend=row.get("backend", ""), worker=worker,
                            item=row.get("item", ""), refused=True,
                            serve_id=serve_id, state=START_UNACKNOWLEDGED,
                            reason="no matching Codex task_started receipt before deadline")
                unacked.append(serve_id)
            elif state == START_UNACKNOWLEDGED and (serve_id, worker) in receipts:
                self.record(row.get("window_id", ""), leg="receipt",
                            backend=row.get("backend", ""), worker=worker,
                            item=row.get("item", ""), refused=True,
                            serve_id=serve_id, state="late_turn_started",
                            reason="late receipt cannot revive a terminal serve")
        return unacked
