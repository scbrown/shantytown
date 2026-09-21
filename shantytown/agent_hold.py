"""Durable per-agent intent: finish this turn, then stop without another feed."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time

from .stopped import FilesStops


def store(root):
    return FilesStops(Path(root) / "agent-holds")


def reason(root, agent):
    if root is None:
        return ""
    record = store(root).get(agent)
    if record is None:
        if (Path(root) / "agent-holds" / f"{agent}.json").exists():
            return "held (hold record unreadable; inspect before release)"
        return ""
    since = datetime.fromtimestamp(record.at, timezone.utc).isoformat(timespec="seconds")
    return f"held (by {record.by or 'operator'}, since {since})"


def hold(root, agent, actor, note=""):
    holds = store(root)
    if holds.get(agent) is None:
        holds.record(agent, time.time(), by=actor, reason=note)
    if holds.get(agent) is None:
        raise OSError("could not persist after-turn hold")


def clear(root, agent):
    holds = store(root)
    holds.forget(agent)
    if (holds.root / f"{agent}.json").exists():
        raise OSError("could not clear after-turn hold")


def finish_turn(root, agent):
    """Run the existing guarded stop outside the pane it is about to kill.

    The Stop hook must survive long enough to persist its event first. A
    detached child also survives the PTY hangup and completes stop bookkeeping.
    Keep the hold until explicit new; a failed stop must never resume feeding.
    """
    record = store(root).get(agent)
    if record is None:
        return False
    log = Path(root) / "agent-holds" / f"{agent}.stop.log"
    with log.open("ab") as output:
        subprocess.Popen(
            [sys.executable, "-m", "shantytown.cli", "--root", str(root),
             "agent", "stop", agent, "--reason",
             f"after-turn hold by {record.by or 'operator'}: {record.reason}"],
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return True
