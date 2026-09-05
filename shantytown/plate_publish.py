"""plate_publish — publish the resolved work item where non-Python consumers can read it.

aegis-qdjof. The epic's premise is "one scope, THREE consumers (policy, trace,
context)", and all three begin by answering *what is this agent working on*.
shantytown owns that answer (``plate(tracker, agent)``), but it owned it only in
Python: there was no env var carrying it and nothing on disk holding it, so hank
(Rust) and bobbin (Rust) could not answer it at all.

WHY A FILE AND NOT THE TWO OBVIOUS ALTERNATIVES
  * NOT `st anchor` per action — a subprocess on every edit and every prompt,
    against the slowest surface there is (it hits the tracker). Rejected on
    aegis-368cu.1.
  * NOT tracker resolution reimplemented in Rust — that is a SECOND backend
    implementation which can disagree with protocols.py. A rule with two
    implementations is the failure aegis-rdclc / aegis-mqnl exist to prevent, and
    it would land in exactly the components whose job is attribution.

So: shantytown PUBLISHES, consumers READ. protocols.py stays the only backend
implementation and neither Rust process learns what a tracker is.

WRITES ARE FAIL-SILENT, and that is stricter than it sounds: publishing is
bookkeeping on the side of a command the operator actually asked for. A full
disk, a read-only root or a lost race must never change what `st anchor` or
`st go` does or prints. Every error here is swallowed whole — the same contract
hank's metrics spool holds, and for the same reason (a bookkeeping write took a
supervisor down once already).

READS ABSTAIN RATHER THAN GUESS. Missing, malformed, or stale all resolve to
None (UNKNOWN), never to a best guess. This is not tidiness: Phase 2 of the epic
REPLAYS these records to derive enforcement rules, so a wrong work item does not
merely mislabel one action — it manufactures a false justification for a rule.
An honest UNKNOWN costs a row of coverage; a confident wrong answer costs the
rule.

STALENESS IS THE SUBTLE ONE. Without it the file is worse than absent: a plate
written when a bead was open keeps answering after that bead closes, so every
later action is attributed to the closed bead — and it looks perfectly
plausible, which is the dangerous kind of wrong. Two independent guards, and a
reader may use either or both:

  * session — if the caller knows its session id and it differs from the one in
    the file, the plate belongs to a previous session. UNKNOWN.
  * newer_than — if the file predates the caller's session start, likewise
    UNKNOWN.

A caller that supplies neither gets whatever is on disk, so callers that CAN
supply one should.
"""

from __future__ import annotations

import json
import os
import tempfile
import os
import time
from pathlib import Path
from typing import Any

__all__ = ["plate_path", "publish", "publish_id", "read", "own_session"]


def own_session() -> str | None:
    """This process's harness session id, or None if it is not in one.

    ONLY for a publisher writing its OWN plate. `st anchor` runs inside the
    agent's session, so this is that agent's session and matches the id the
    pre-edit hook payload carries — verified 2026-09-05: a live guard record
    for a real Write carried exactly $CLAUDE_CODE_SESSION_ID.

    A DISPATCHER MUST NOT USE THIS. `st go` writes another agent's plate from
    the dispatcher's own process, so this would stamp the wrong session onto
    the recipient's plate and make the reader abstain on every dispatched
    item — turning a staleness guard into a total attribution outage. That
    caller keeps writing None, which means "not session-scoped" and is checked
    only against `at`.
    """
    value = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    return value or None


def plate_path(root: Path | str, agent: str) -> Path:
    """`<root>/crew/<agent>/plate.json`.

    Deliberately a directory PER AGENT rather than `crew/<agent>.plate.json`:
    the card at `crew/<agent>.json` is registry data that a human edits and git
    may track, while the plate is volatile per-session state a machine
    rewrites. Keeping them in different nodes of the tree means a careless
    glob (`crew/*.json`) cannot pick the plate up as if it were a card.
    """
    return Path(root) / "crew" / agent / "plate.json"


def publish(
    root: Path | str,
    agent: str,
    item: Any | None,
    session: str | None = None,
    _now: float | None = None,
) -> bool:
    """Write the agent's current work item. Returns True if it landed.

    `item` is a WorkItem or None. **None is published, not skipped** — "this
    agent has an empty plate" is a FACT and the consumers need it. Skipping the
    write would leave the previous item standing, which is precisely the
    attributed-to-a-closed-bead failure the staleness rule exists to stop; here
    we can prevent it at the source instead of detecting it later.

    The write is atomic (temp file + os.replace) so a reader never observes a
    half-written file. Readers are on fail-silent paths and would abstain on a
    truncated read anyway, but abstaining is a lost record — atomicity means we
    do not spend one.

    Never raises. Returns False if the write did not land, for a caller that
    wants to log it; no caller is obliged to look.
    """
    item_id = getattr(item, "id", None) if item is not None else None
    return publish_id(root, agent, item_id, session=session, _now=_now)


def publish_id(
    root: Path | str,
    agent: str,
    item_id: str | None,
    session: str | None = None,
    _now: float | None = None,
) -> bool:
    """[`publish`] for a caller that holds an ID rather than a WorkItem.

    The dispatcher is that caller: its Plan carries `item_id` as a string,
    because a Plan describes what a dispatch WOULD do and never needed the row
    itself. Rather than have it manufacture an object with an `.id` to satisfy
    a getattr, both entry points meet here — ONE writer, so the payload shape
    and the atomic-replace cannot drift into two versions.
    """
    try:
        payload = {
            "item": item_id,
            "at": int(_now if _now is not None else time.time()),
            "session": session,
        }
        path = plate_path(root, agent)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Same directory as the target: os.replace is only atomic within a
        # filesystem, and /tmp is routinely a different one.
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".plate-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh)
            os.replace(tmp, path)
        except BaseException:
            # Do not leave debris behind a failed publish.
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except BaseException:
        # Fail-silent, deliberately including non-Exception BaseExceptions:
        # bookkeeping must not convert an operator's interrupt into a traceback
        # from a code path they did not ask about.
        return False


def read(
    root: Path | str,
    agent: str,
    session: str | None = None,
    newer_than: float | None = None,
) -> str | None:
    """The agent's current work-item id, or None for UNKNOWN.

    None means "I could not tell", and callers must treat it that way rather
    than as "no work". The two are different facts and only one of them is
    safe to record as attribution.

    UNKNOWN is returned for: no file, unreadable file, malformed JSON, a
    non-string item, an explicitly-null item (an empty plate is not a work
    item), a session mismatch, or a timestamp older than `newer_than`.
    """
    try:
        with open(plate_path(root, agent)) as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None
        item = data.get("item")
        if not isinstance(item, str) or not item:
            return None
        # A NULL stored session means "not session-scoped", NOT "belongs to no
        # session" — a dispatcher writes it that way because it cannot know the
        # recipient's session. Rejecting those would make every DISPATCHED plate
        # unreadable the moment a reader supplies a session, converting a
        # staleness guard into a total attribution outage. Only a stored session
        # that DISAGREES is a mismatch.
        stored = data.get("session")
        if session is not None and stored is not None and stored != session:
            return None
        if newer_than is not None:
            at = data.get("at")
            if not isinstance(at, (int, float)) or at < newer_than:
                return None
        return item
    except BaseException:
        return None
