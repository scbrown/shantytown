"""Opt-in, private evidence for human labelling; never an inference or a feed.

The event stream records turn boundaries, not process deaths. Keep that distinction
in the sample and never attach pane text to an event that can leave this host.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
import time

LIMIT = 30
RETENTION_SECONDS = 30 * 24 * 60 * 60
TAIL_BYTES = 8192


def _private_fd(path: Path, flags: int) -> int:
    # Check AFTER open to avoid a path race, but open nonblocking: a FIFO can
    # otherwise hang before fstat gets the chance to reject its file type.
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        os.close(fd)
        raise ValueError("sample file must be private, owned and unlinked")
    return fd


def collect(root: Path, event: dict | None, read_tail=None, *, now=None) -> str:
    """Append one sample, or expire the collection when event is None.

    One collection per deployment, 30 total (not 30 per process). Expiry removes
    text but keeps a closed marker, so repeated hooks cannot silently refill it.
    A busy collector is skipped, never allowed to delay stop delivery.
    """
    now = time.time() if now is None else now
    directory = Path(root) / "stop_samples"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("sample directory must be private and owned")
    lock = _private_fd(directory / "lock", os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "busy"
        # A killed writer may leave its private staging file. It is never an
        # additional sample or an archive exempt from the retention sweep.
        for stale in directory.glob(".collection-*"):
            stale.unlink()
        path = directory / "collection.json"
        try:
            with os.fdopen(_private_fd(path, os.O_RDONLY)) as stream:
                collection = json.load(stream)
        except FileNotFoundError:
            if event is None:
                return "absent"
            collection = {"schema": 1, "started_at": now,
                          "expires_at": now + RETENTION_SECONDS,
                          "closed": False, "samples": []}
        # Refuse malformed state instead of resetting it and collecting forever.
        if (collection.get("schema") != 1 or not isinstance(collection.get("samples"), list)
                or not isinstance(collection.get("closed"), bool)
                or not isinstance(collection.get("expires_at"), (float, int))):
            raise ValueError("invalid sample collection")
        samples = collection["samples"]
        if now >= collection["expires_at"]:
            collection.update(closed=True, samples=[])
            outcome = "expired"
        elif collection["closed"] or len(samples) >= LIMIT:
            return "full"
        elif event is None:
            return "retained"
        elif any(s["event_id"] == event["event_id"] for s in samples):
            return "duplicate"
        else:
            tail = read_tail()
            if not isinstance(tail, str) or not tail:
                return "unavailable"
            raw = tail.encode("utf-8")
            samples.append({**event, "captured_at": now,
                            "boundary_kind": "turn-stop-hook",
                            "pane_tail": raw[-TAIL_BYTES:].decode("utf-8", errors="ignore"),
                            "tail_truncated": len(raw) > TAIL_BYTES,
                            "expected": None, "label_author": None})
            outcome = "captured"
        # mkstemp creates 0600 before any pane bytes are written. Atomic replace
        # prevents a crash leaving partially written evidence that looks empty.
        fd, temporary = tempfile.mkstemp(prefix=".collection-", dir=directory)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(collection, stream, ensure_ascii=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return outcome
    finally:
        os.close(lock)


def main(argv=None) -> int:
    """Local retention sweep; prints only status, never sample content."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(collect(args.root, None))
    except Exception as exc:
        # Exceptions can contain input text; do not leak their message.
        print(f"stop samples unavailable: {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
