"""Adapter for Creel's canonical fleet setpoint advisory record.

This module deliberately contains no controller arithmetic.  It serializes the
usage evidence Shantytown already has, invokes Creel's headless reader, and
returns the reader's ``controller_line`` verbatim (collapsed to one display
line).  Missing dependencies and malformed output are data, never a zero.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


PROBE_ENV = "SHANTY_CREEL_ADMISSION_PROBE"


class Alerter:
    """Push changed advisory records to the administrator, once per episode."""

    def __init__(self, root, reg, panes, *, push=None):
        self.path = Path(root) / "notify" / "governor_advisory.json"
        self.reg = reg
        self.panes = panes
        if push is None:
            from .notify import push_to_admin
            push = push_to_admin
        self.push = push

    def sweep(self, lines: dict[str, str]) -> list[str]:
        try:
            previous = json.loads(self.path.read_text())
        except (OSError, ValueError):
            previous = {}
        def recommendation(line: str) -> int | None:
            match = re.search(r"\bgovernor recommends ([+-]?\d+)\b", line)
            return int(match.group(1)) if match else None

        def key(line: str) -> str:
            delta = recommendation(line)
            if delta is not None:
                return f"delta:{delta}"
            if line.startswith("advisory unavailable:"):
                return "unavailable"
            return f"record:{line}"

        def previous_key(name: str) -> str | None:
            old = previous.get(name)
            if not isinstance(old, str):
                return None
            # Migrate the original line-valued ledger without re-alerting a
            # hold merely because its storage representation changed.
            return old if old.startswith(("delta:", "record:")) or old == "unavailable" else key(old)

        changed = []
        for name, line in sorted(lines.items()):
            delta = recommendation(line)
            if delta not in (None, 0) or previous_key(name) != key(line):
                changed.append(name)
        sent = []
        for name in changed:
            if self.push(self.reg, self.panes,
                         f"governor setpoint [{name}]: {lines[name]}"):
                sent.append(name)
        if sent:
            from .files import write_json_atomic
            updated = dict(previous)
            updated.update({name: key(lines[name]) for name in sent})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self.path, updated)
        return sent


def _unavailable(why: str) -> str:
    return f"advisory unavailable: {why}"


def controller_line(readings, *, running: int, cap: int | None,
                    probe: str | None = None, node: str | None = None,
                    now: float | None = None, run=subprocess.run) -> str:
    """Return Creel's line, or an explicit unavailable result.

    ``readings`` are Shantytown ``Reading`` objects.  Their timestamps and reset
    times are preserved so Creel—not this adapter—decides whether evidence is
    fresh enough to recommend a change.
    """
    probe = probe or os.environ.get(PROBE_ENV)
    if not probe:
        return _unavailable(f"creel probe not configured ({PROBE_ENV})")
    probe_path = Path(probe)
    if not probe_path.is_file():
        return _unavailable("creel probe not found")
    node = node or shutil.which("node")
    if not node:
        return _unavailable("node not found")

    clock = int(now if now is not None else time.time())
    state = {"readings": {}}
    for window, reading in readings.items():
        item = {
            "pct": reading.pct,
            "at": reading.at,
            "source": reading.source,
            "resetAt": reading.reset_at,
            "limitId": reading.limit_id,
        }
        if not reading.ok:
            item["error"] = reading.error or "usage reading unavailable"
        state["readings"][window] = {k: v for k, v in item.items() if v is not None}

    path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json",
                                         delete=False) as fh:
            json.dump(state, fh)
            path = fh.name
        cmd = [node, str(probe_path), "--state", path, "--running", str(running),
               "--now", str(clock), "--quiet"]
        if cap is not None:
            cmd.extend(["--cap", str(cap)])
        completed = run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return _unavailable(type(exc).__name__)
    finally:
        if path:
            try:
                Path(path).unlink()
            except OSError:
                pass

    # Exit 1/2 are policy/instrument verdicts with a valid record.  Exit 3 is a
    # broken invocation and must not be presented as an advisory.
    if completed.returncode not in (0, 1, 2):
        return _unavailable("creel probe failed")
    try:
        record = json.loads(completed.stdout)
        line = record["controller_line"].strip()
    except (ValueError, KeyError, AttributeError):
        return _unavailable("creel probe returned no controller record")
    if not line:
        return _unavailable("creel probe returned an empty advisory")
    return " · ".join(part.strip() for part in line.splitlines() if part.strip())
