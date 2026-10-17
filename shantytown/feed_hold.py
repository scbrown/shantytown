"""Per-agent automatic-feed intent, independent of pane lifecycle and dispatch."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import re
import time

from .files import write_json_atomic


def _path(root, agent):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", agent):
        raise ValueError("agent must be an identity, not a path")
    return Path(root) / "feed-holds" / (agent + ".json")


def expiry(until):
    """Return a UTC timestamp or an exact closed-item condition."""
    if until.startswith("closed:"):
        identifier = until.removeprefix("closed:")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identifier):
            raise ValueError("closed condition requires an exact item ID")
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", until):
            stamp = datetime.fromisoformat(until).replace(tzinfo=timezone.utc)
        else:
            stamp = datetime.fromisoformat(until.replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
                raise ValueError("expiry must be UTC")
        return stamp.timestamp()
    except (ValueError, OverflowError) as exc:
        raise ValueError("until requires a UTC date/time or closed:<exact-id>") from exc


@contextmanager
def _lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / ".lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def hold(root, agent, actor, until, note, *, now=None):
    now = time.time() if now is None else now
    deadline = expiry(until)
    if deadline is not None and deadline <= now:
        raise ValueError("feed hold expiry must be in the future")
    if not isinstance(note, str) or not note.strip() or len(note.encode()) > 4096:
        raise ValueError("feed hold requires a nonempty reason of at most 4096 bytes")
    path = _path(root, agent)
    record = dict(schema="st.feed-hold/1", agent=agent, by=actor, at=now,
                  until=until, reason=note.strip())
    with _lock(path):
        if path.is_symlink():
            raise ValueError("feed hold record must not be a symlink")
        write_json_atomic(path, record)
        if json.loads(path.read_text()) != record:
            raise OSError("feed hold read-back unproven; inspect before retrying")
    return record


def clear(root, agent):
    path = _path(root, agent)
    with _lock(path):
        path.unlink(missing_ok=True)
        if path.exists() or path.is_symlink():
            raise OSError("feed hold clear read-back unproven")


def _closed(root, identifier):
    # Reuse the selected deployment's exact-item read, including the Seeds
    # metadata route. Never hydrate a board or silently choose another backend.
    from . import cli, work_item
    args = cli.build_parser().parse_args(
        ["--root", str(root), "work", "item", "show", identifier])
    item = work_item.command(args, cli._tracker(args))
    if item["id"] != identifier:
        raise ValueError("resume read answered a different item")
    return item["status"] == "closed"


def reason(root, agent, *, now=None, closed=None):
    if root is None:
        return ""
    path = _path(root, agent)
    try:
        if not path.exists() and not path.is_symlink():
            return ""
        with _lock(path):
            if path.is_symlink() or path.stat().st_size > 32768:
                raise ValueError("invalid feed hold record")
            record = json.loads(path.read_text())
        if (record.get("schema") != "st.feed-hold/1" or record.get("agent") != agent
                or not isinstance(record.get("reason"), str) or not record["reason"].strip()
                or not isinstance(record.get("by"), str)):
            raise ValueError("invalid feed hold record")
        until = record["until"]
        deadline = expiry(until)
        if deadline is not None:
            if (time.time() if now is None else now) >= deadline:
                return ""
        else:
            try:
                resumed = (closed or (lambda identifier: _closed(root, identifier)))(
                    until.removeprefix("closed:"))
                if resumed is True:
                    return ""
                if resumed is not False:
                    raise ValueError("resume status unavailable")
            except Exception:
                return "feed held (resume condition UNKNOWN; inspect before release)"
        # Expired records remain as provenance. No read can delete a newer hold
        # written concurrently, and an old expiry can never clear a replacement.
        return f"feed held (by {record['by'] or 'operator'}, until {until}): {record['reason']}"
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return "feed held (record unreadable; inspect before release)"
