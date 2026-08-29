"""Transactional fleet-maintenance windows.

One JSON document is both the journal and the relaunch lease.  There is never a
second "active" marker whose lifetime can disagree with the manifest it guards.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .files import write_json_atomic


class WindowRefused(RuntimeError):
    """A known precondition failed; the caller may safely correct and retry."""


class WindowUnreadable(RuntimeError):
    """The ledger or a required observation could not be read."""


@dataclass(frozen=True)
class WindowStore:
    root: Path

    @property
    def path(self) -> Path:
        return Path(self.root) / "window" / "active.json"

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text())
        except (OSError, ValueError) as exc:
            raise WindowUnreadable(f"maintenance-window ledger unreadable: {exc}") from exc
        if not isinstance(value, dict) or not value.get("id"):
            raise WindowUnreadable("maintenance-window ledger has no window id")
        return value

    def create(self, manifest: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL is the exclusivity primitive.  A look-then-write would permit two
        # simultaneous plan IDs to both observe absence and overwrite each other.
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            current = self.load()
            raise WindowRefused(
                f"maintenance window {current['id']!r} already exists; "
                "release or abort it before planning another") from exc
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(manifest, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            self.path.unlink(missing_ok=True)
            raise

    def update(self, expected_id: str, **changes) -> dict:
        current = self.require(expected_id)
        current.update(changes)
        write_json_atomic(self.path, current)
        return current

    def require(self, expected_id: str) -> dict:
        current = self.load()
        if current is None:
            raise WindowRefused("no maintenance window is planned")
        if current["id"] != expected_id:
            raise WindowRefused(
                f"active maintenance window is {current['id']!r}, not {expected_id!r}")
        return current

    def finish(self, expected_id: str) -> dict:
        current = self.require(expected_id)
        self.path.unlink()
        return current


def active(root: Path) -> dict | None:
    """Return the lease, failing closed when its ledger is malformed."""
    return WindowStore(Path(root)).load()


def plan(root: Path, window_id: str, *, roster: list[dict], anchors: list[dict],
         deployed_sha: str, target_version: str | None, timer: dict,
         actor: str = "", now: float | None = None, persist: bool = True) -> dict:
    if not window_id.strip():
        raise WindowRefused("window id must not be empty")
    if target_version and deployed_sha.removesuffix("-dirty").startswith(target_version):
        raise WindowRefused(
            f"target version {target_version} is already installed ({deployed_sha}); "
            "refusing before drain")
    manifest = {
        "id": window_id,
        "state": "planned",
        "planned_at": float(time.time() if now is None else now),
        "actor": actor,
        "deployed_sha": deployed_sha,
        "target_version": target_version or "",
        "roster": roster,
        "anchors": anchors,
        "timer": timer,
    }
    if persist:
        WindowStore(Path(root)).create(manifest)
    return manifest


def drain(root: Path, window_id: str, *, pause_timer) -> dict:
    store = WindowStore(Path(root))
    current = store.require(window_id)
    if current["state"] not in ("planned", "draining"):
        raise WindowRefused(f"window {window_id!r} is {current['state']}, not planned")
    pause_timer()
    return store.update(window_id, state="draining", drained_at=time.time())


def clear(root: Path, window_id: str, *, observe, persist: bool = True) -> dict:
    store = WindowStore(Path(root))
    current = store.require(window_id)
    if current["state"] not in ("draining", "clear"):
        raise WindowRefused(f"window {window_id!r} has not entered drain")
    blockers = list(observe(current))
    if blockers:
        raise WindowRefused("CLEAR refused; still live/writing: " + "; ".join(blockers))
    return (store.update(window_id, state="clear", cleared_at=time.time())
            if persist else dict(current, state="clear"))


def restore(root: Path, window_id: str, *, start_agent, is_live, timer_active,
            resume_timer, require_clear: bool) -> dict:
    store = WindowStore(Path(root))
    current = store.require(window_id)
    if require_clear and current["state"] != "clear":
        raise WindowRefused("release requires a successful CLEAR; use abort to roll back earlier")
    # Keep the lease throughout restoration. A delete-then-restore gap would let
    # another process acquire a second ID (or tend relaunch a wider roster) while
    # the first transaction was still in flight.
    store.update(window_id, state="restoring", restoring_at=time.time())
    try:
        wanted = [r for r in current["roster"] if r.get("live")]
        for row in wanted:
            start_agent(row["agent"])
        if current.get("timer", {}).get("active"):
            resume_timer()
        missing = [r["agent"] for r in wanted if not is_live(r["agent"])]
        if missing:
            raise WindowUnreadable("restored roster read-back missing: " + ", ".join(missing))
        if bool(timer_active()) != bool(current.get("timer", {}).get("active")):
            raise WindowUnreadable("tend timer read-back does not match pre-window state")
    except Exception:
        store.update(window_id, state="restore_failed", restore_failed_at=time.time())
        raise
    store.finish(window_id)
    return current
