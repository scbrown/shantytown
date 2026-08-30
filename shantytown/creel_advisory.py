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
from dataclasses import dataclass
from pathlib import Path


PROBE_ENV = "SHANTY_CREEL_ADMISSION_PROBE"


@dataclass(frozen=True)
class Advice:
    """One advisory ready to push: what to SAY, what to dedup ON, and whether it
    is still actionable.

    The three are separate because they move at different rates.  The line
    carries live numbers that change every pass; the key carries only the
    recommendation; actionability decides whether a standing recommendation keeps
    asking or goes quiet once it has been read.
    """

    line: str
    key: str
    actionable: bool = False
    """Re-push even when the key has NOT changed. OFF for every producer today.

    sattler's ox5dh ruling: push on first occurrence and on value change, silent
    on all repeats including a nonzero one. The field survives the ruling as a
    dormant hook — if a nonzero recommendation is ever MEASURED sitting
    unactioned, a slow re-nag is this flag plus a clock rather than a redesign.
    Defaulting it False means a new producer gets the ruling by construction.
    """


def recommended_delta(line: str) -> int | None:
    """Creel's recommended agent delta, read off the line Creel published.

    The line IS Creel's record, so reading it is CONSUMING that record rather
    than forming a second opinion about the budget — the distinction the
    aegis-rjrwu ruling turns on.  None means the line carries no recommendation
    at all: an unavailable advisory, or a record shape this reader predates.
    """
    match = re.search(r"\bgovernor recommends ([+-]?\d+)\b", line)
    return int(match.group(1)) if match else None


def _creel_advice(line: str) -> Advice:
    delta = recommended_delta(line)
    if delta is not None:
        key = f"delta:{delta}"
    elif line.startswith("advisory unavailable:"):
        key = "unavailable"
    else:
        key = f"record:{line}"
    # SILENT ON EVERY REPEAT, including a nonzero one (sattler's ruling on
    # aegis-ox5dh, 2026-08-29, superseding gennaro's 1641346 rule that a nonzero
    # delta stays actionable every pass).
    #
    # 1641346's instinct was right — an unactuated recommendation must not go
    # silent — and the cadence was what made it wrong. Measured on the live
    # surface: the utilization line pushed the SAME +3 at 20:27, 20:32 and 20:37
    # while the standing answer was known and deliberate. A channel that repeats
    # itself is one its reader learns to ignore (aegis-3w0br), which un-builds
    # the advisory. The standing state is carried by `st crew --governor`, which
    # is a place you LOOK rather than a thing that interrupts you.
    #
    # `actionable` is kept rather than deleted because the re-nag may come back:
    # if a nonzero recommendation is ever MEASURED sitting unactioned, a slow one
    # is this flag plus a clock, not a redesign. It is not speculation left armed
    # — it is off, and turning it on requires the measurement first.
    return Advice(line=line, key=key, actionable=False)


def _looks_like_a_creel_line(value: str) -> bool:
    """Is this a stored LINE from before the ledger held keys, or a key already?

    INVERTED ON PURPOSE, and the inversion is the whole point. This was first
    written as a whitelist of known key PREFIXES, which silently requires every
    future producer to add its own: the utilization advisory's `cause` labels
    (`over-pace:`, `at-cap:`, …) were not on that list, so every hold failed the
    comparison and re-pushed ON EVERY PASS — an infinite re-page, strictly worse
    than the duplicate it was added to fix. Caught by replaying the real ledger
    before deploying, not by the unit tests, which used `fill:` keys throughout.

    A legacy value is RECOGNISABLE — it is a Creel sentence carrying a
    recommendation, or an explicit unavailability. Everything else is already a
    key, whoever produced it. A new producer now needs to do nothing.
    """
    return (recommended_delta(value) is not None
            or value.startswith("advisory unavailable:"))


class Alerter:
    """Push changed advisory records to the administrator, once per episode.

    Generalised for a second producer (aegis-967a9): the utilization advisory
    keys on a structured recommendation rather than on Creel's sentence, so it
    passes `Advice` directly instead of having its rendered line re-parsed.  A
    plain string still means "a Creel line", which keeps the original call site
    byte-identical.
    """

    def __init__(self, root, reg, panes, *, push=None,
                 filename="governor_advisory.json", label="governor setpoint"):
        self.path = Path(root) / "notify" / filename
        self.reg = reg
        self.panes = panes
        self.label = label
        if push is None:
            from .notify import push_to_admin
            push = push_to_admin
        self.push = push

    def sweep(self, items) -> list[str]:
        try:
            previous = json.loads(self.path.read_text())
        except (OSError, ValueError):
            previous = {}
        advices = {name: (item if isinstance(item, Advice) else _creel_advice(item))
                   for name, item in items.items()}

        def previous_key(name: str) -> str | None:
            old = previous.get(name)
            if not isinstance(old, str):
                return None
            # Migrate the original line-valued ledger without re-alerting a hold
            # merely because its storage representation changed.
            return _creel_advice(old).key if _looks_like_a_creel_line(old) else old

        changed = [name for name, adv in sorted(advices.items())
                   if adv.actionable or previous_key(name) != adv.key]
        sent = []
        for name in changed:
            if self.push(self.reg, self.panes,
                         f"{self.label} [{name}]: {advices[name].line}"):
                sent.append(name)
        if sent:
            from .files import write_json_atomic
            updated = dict(previous)
            updated.update({name: advices[name].key for name in sent})
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
