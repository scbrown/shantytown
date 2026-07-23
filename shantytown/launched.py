"""launched — what settings each live agent ACTUALLY came up on.

WHY THIS EXISTS (measured 2026-07-19).

`--settings` is read ONCE, at launch. Every later rewrite of that file lands on
disk and reaches NOBODY who is already running. The failure is silent in both
directions and both directions were observed the same night:

  - A FIX that does not arrive. The Stop hook was corrected to carry an absolute
    `--root`; agents launched before the rewrite kept the old unrooted command,
    so their stop events resolved against their own workspace (which has no
    .shanty) and died. kelly and gennaro stayed "up" in `st crew`, kept working,
    kept committing — and the administrator at the root of the tier could not
    hear either of them. Nothing anywhere said so.

  - A BREAK that does not detonate. A PreToolUse guard was added that hard-blocks
    every edit. The fleet stayed green for half an hour, not because the guard was
    safe but because nobody had relaunched into it. The first agent to restart —
    for an unrelated reason — was the first to discover it, with its body.

So a settings file that has been live for hours over a green fleet has NOT been
shown to work. It has been shown that nobody has run it. This module is what lets
`st crew` say which of those two it is looking at.

THE ANSWER HAS THREE VALUES, NOT TWO. `unknown` is a first-class result and is
never rounded down to "fine": an agent launched before stamping existed, or by
something other than `st new`, has no stamp, and the honest report is that we
cannot tell. The whole bug being detected here is a false clean bill of health;
a detector that invented one would be the same disease in a new place.

DELIBERATELY NOT AN IDENTITY FIELD. This is per-LAUNCH runtime state, not who the
agent IS, so it lives in its own store beside events/ rather than on the card.
Stamps are keyed by agent name and overwritten on each launch — the previous
launch's settings are of no interest, only the running one's.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

# The three verdicts. Strings, because they are for humans reading `st crew`.
CURRENT = "current"     # stamped, and the file still hashes to what it launched on
STALE = "STALE"         # stamped, and the file has CHANGED since -> hooks are old
UNKNOWN = "unknown"     # no stamp -> we did not look / could not tell. NOT "fine".


def digest(path) -> str | None:
    """sha256 of a settings file, or None if it is not readable.

    Content, not mtime. A settings file gets REWRITTEN idempotently by
    `_emit_role_settings` on every `st project` / `role set`, so mtime changes
    constantly while the bytes stay identical. Hashing mtime would cry stale at a
    whole fleet that is perfectly current, and a detector that cries wolf is one
    that gets ignored right up until the night it is right.
    """
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


@dataclass(frozen=True)
class Stamp:
    """What one agent launched on."""
    settings: str        # path to the settings file it was launched with
    sha256: str          # the file's content hash AT LAUNCH


class FilesLaunches:
    """Launch stamps in a directory of json. Same floor as FilesRegistry."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def record(self, agent: str, settings_path) -> Stamp | None:
        """Stamp what `agent` was just launched with. Returns the stamp, or None
        if the settings file could not be hashed.

        Called AFTER the launch is delivered, and its failure must never fail the
        launch: an unstamped agent reports `unknown`, which is exactly the state
        it is in. Losing a stamp costs us a detection; refusing to launch over one
        would cost us the agent.
        """
        h = digest(settings_path)
        if h is None:
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = Stamp(settings=str(settings_path), sha256=h)
        # Atomic (internal-ref): a stamp torn by a killed writer would make the
        # settings verdict a confident lie about a measurement that never
        # completed — the one thing a stamp must never be.
        from .files import write_json_atomic
        write_json_atomic(self.root / f"{agent}.json",
                          {"settings": stamp.settings, "sha256": stamp.sha256})
        return stamp

    def get(self, agent: str) -> Stamp | None:
        p = self.root / f"{agent}.json"
        if not p.is_file():
            return None
        try:
            d = json.loads(p.read_text())
            return Stamp(settings=d["settings"], sha256=d["sha256"])
        except (OSError, ValueError, KeyError):
            # An unreadable stamp is not a clean one. Fall through to unknown.
            return None

    def forget(self, agent: str) -> None:
        """Drop the stamp — the agent is no longer running on it.

        `st stop` calls this. A stamp left behind after a stop would describe a
        process that no longer exists, and the next `st crew` would happily report
        `current` for a dead agent's settings. Stamps describe LIVE launches only.
        """
        self.root.joinpath(f"{agent}.json").unlink(missing_ok=True)

    def verdict(self, agent: str) -> str:
        """CURRENT | STALE | UNKNOWN for one agent, probed NOW.

        Read live, every time, from the file as it stands at the moment of asking
        — never cached, and never inferred from an artifact of some past success.
        That distinction is not pedantry; it is the exact mistake this bead's
        author made by hand. A persisted stop event from 20:36 was taken as proof
        that agent's hook worked, and it did work — at 20:36. The agent had been
        deaf for the forty minutes since. One past success is a fact about a
        moment, never a property of a process.
        """
        stamp = self.get(agent)
        if stamp is None:
            return UNKNOWN
        now = digest(stamp.settings)
        if now is None:
            return UNKNOWN          # the file it launched on is gone -> can't tell
        return CURRENT if now == stamp.sha256 else STALE
