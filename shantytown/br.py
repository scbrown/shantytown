"""br — SQLite+JSONL tracker backend, beside the legacy bd adapter."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

from .beads import (BeadsTracker, _PLATE_RANK, _priority, plate_key,
                    ready_ids_or_none, name_the_blocker)
from .protocols import is_blocked
from .inbox import is_message, is_unworkable
from .protocols import BLOCKER_KIND_LABELS, WorkItem


class BrTracker(BeadsTracker):
    """The three-operation Tracker protocol implemented through ``br``."""

    def _bd_in(self, repo: "str | None", *args: str) -> subprocess.CompletedProcess:
        cmd = [os.environ.get("SHANTY_BR_BIN", "br"), *args]
        return subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                              timeout=self.timeout)

    def _bd(self, *args: str) -> subprocess.CompletedProcess:
        # Defined here (rather than inherited) so bd-specific test guards and
        # monkeypatches cannot accidentally turn a br call into a bd call.
        return self._bd_in(self.repo, *args)

    def update(self, item_id: str, **fields) -> None:
        selected = fields.pop("blocker_kind", None)
        reason = fields.pop("defer_reason", None)
        status = fields.pop("status", None)

        # br protects terminal transitions behind dedicated verbs.
        if status == "closed":
            r = self._bd_for(item_id, "close", item_id, "--json")
            if r.returncode != 0:
                raise RuntimeError(
                    f"br close {item_id} failed: {r.stderr.strip()[:120]}")
            if not fields and selected is None and reason is None:
                return

        args = ["update", item_id]
        if status is not None and status != "closed":
            args.append(f"--status={status}")
        for key, value in fields.items():
            if value is not None:
                args.append(f"--{key.replace('_', '-')}={value}")
        if selected is not None:
            args.append(f"--add-label={selected}")
            args.extend(
                f"--remove-label={old}"
                for old in sorted(set(BLOCKER_KIND_LABELS.values()) - {selected}))
        if reason is not None:
            args.append(f"--notes={reason}")
        if len(args) == 2:
            return
        r = self._bd_for(item_id, *args)
        if r.returncode != 0:
            raise RuntimeError(
                f"br update {item_id} failed: {r.stderr.strip()[:120]}")


def _failure_reason(r) -> str:
    """The human reason a br call failed.

    br reports failures as a JSON envelope on STDOUT and frequently leaves
    STDERR EMPTY, so the obvious `r.stderr` read renders a real fault as a blank
    reason. That is how the NA store took `st anchor` down fleet-wide while the
    only message anyone saw ended at the colon with nothing after it
    (aegis-r2isg's shantytown seam) - an outage that named its own cause and
    still read as a mystery. Prefer the envelope, fall back to stderr, and never
    return "" so a caller can always print something.
    """
    try:
        err = (json.loads(r.stdout) or {}).get("error") or {}
        code, msg = err.get("code"), err.get("message")
        if code or msg:
            return f"{code}: {msg}" if code and msg else str(code or msg)
    except (ValueError, AttributeError, TypeError):
        pass
    return r.stderr.strip()[:200] or f"br exited {r.returncode} with no message"


def rows_partial(tracker: BrTracker) -> "tuple[list[dict], list[str]]":
    """Rows from every READABLE store, plus one named note per store that failed.

    The union-query contract (`rows()`, below) is unchanged and still refuses a
    partial answer. This is the seam underneath it, for the one caller that must
    survive an unreadable store: `plate()`. See its docstring for why the answer
    there is "degrade LOUDLY", not "raise" and not "shorten quietly".
    """
    out: list[dict] = []
    failures: list[str] = []
    repos = tracker.repos or [None]
    for repo in repos:
        r = tracker._bd_in(repo, "list", "--json", "--limit", "0")
        if r.returncode != 0:
            failures.append(
                f"br list failed for store {repo or '(default)'}: "
                f"{_failure_reason(r)}")
            continue
        payload = json.loads(r.stdout) if r.stdout.strip() else {}
        out.extend(payload.get("issues", []) if isinstance(payload, dict) else payload)
    return out, failures


def rows(tracker: BrTracker) -> list[dict]:
    """Every issue in every configured br store, refusing partial unions."""
    out, failures = rows_partial(tracker)
    if failures:
        raise RuntimeError(failures[0])
    return out


def ready(tracker: BrTracker) -> list[dict]:
    """The complete br ready set, preserving dependency filtering."""
    r = tracker._bd("ready", "--json", "--limit", "0")
    if r.returncode != 0:
        raise RuntimeError(f"br ready failed: {r.stderr.strip()[:120]}")
    payload = json.loads(r.stdout) if r.stdout.strip() else []
    return payload.get("issues", []) if isinstance(payload, dict) else payload


def in_progress(tracker: BrTracker) -> list[dict]:
    """The complete active-anchor set from br."""
    r = tracker._bd("list", "--status", "in_progress", "--json", "--limit", "0")
    if r.returncode != 0:
        raise RuntimeError(f"br list failed: {r.stderr.strip()[:120]}")
    payload = json.loads(r.stdout) if r.stdout.strip() else {}
    return payload.get("issues", []) if isinstance(payload, dict) else payload


def _warn_stderr(note: str) -> None:
    """Default degradation channel: loud, unmissable, and impossible to forget.

    A caller silences this by passing its own `warn`, never by omission.
    """
    print(f"  \u26a0 PLATE INCOMPLETE - {note}", file=sys.stderr)


def plate(tracker: BrTracker, agent: str,
          warn: "callable | None" = None) -> WorkItem | None:
    """The agent's ONE held item, surviving an unreadable extra store.

    WHY THIS DOES NOT RAISE, AND DOES NOT GO QUIET (aegis-r2isg seam).

    `rows()` refuses a partial union and that is correct FOR A QUERY: a shorter
    answer at exit 0 is a wrong answer, not a small one. But `plate()` is not a
    query, it is the FIRST STEP OF EVERY CREW SESSION - `st anchor`, the stop
    event, the governor and the dashboard all read it. Raising there converted
    one unreadable store into a fleet-wide outage of the propulsion loop:
    measured 2026-08-30, `st anchor` died with a traceback for EVERY agent while
    the primary store - the one holding essentially every plate - was healthy.

    So the answer is the third one: return the plate we can actually see, and
    say LOUDLY which store we could not read. `warn` defaults to a stderr print,
    so silence has to be asked for explicitly; a caller that forgets is loud, not
    quiet. That keeps the aegis-tisp property the raise was protecting - an
    agent holding an item in the unreadable store still never reads as a clean
    empty plate, because the warning names the store the answer is missing.
    """
    seen, failures = rows_partial(tracker)
    for note in failures:
        (warn or _warn_stderr)(note)
    mine = [
        row for row in seen
        if row.get("assignee") in (agent, agent.split("/")[-1])
        and row.get("status") != "closed"
        and not is_message(row.get("title", ""))
        and not is_unworkable(row)   # the ROW: deferral is a FIELD on br (aegis-vyc3aa)
    ]
    if not mine:
        return None
    # Readiness is read from the SAME tracker the rows came from, and a failure
    # degrades to None (previous ordering) rather than to an empty set — the
    # could-not-look-is-not-empty rule this function's docstring is built on
    # applies to the readiness call exactly as it does to the rows.
    ready_ids = ready_ids_or_none(tracker)
    mine.sort(key=lambda row: plate_key(row.get("status"), _priority(row),
                                        row.get("id", ""), ready_ids))
    row = mine[0]
    item = WorkItem(id=row.get("id", ""), title=row.get("title", ""),
                    status=row.get("status", "open"),
                    assignee=row.get("assignee"), priority=_priority(row))
    # Same rule as beads.plate: a blocked plate must name its blocker. One extra
    # read, only in the case that would otherwise burn a whole turn.
    if is_blocked(row.get("status"), row.get("id", ""), ready_ids):
        item = name_the_blocker(tracker, item)
    return item


def items(tracker: BrTracker) -> list[WorkItem]:
    """Every item in the primary store, for durable inbox reads."""
    r = tracker._bd("list", "--json", "--limit", "0")
    if r.returncode != 0:
        raise RuntimeError(f"br list failed: {r.stderr.strip()[:120]}")
    payload = json.loads(r.stdout) if r.stdout.strip() else {}
    source = payload.get("issues", []) if isinstance(payload, dict) else payload
    return [WorkItem(id=x.get("id", ""), title=x.get("title", ""),
                     status=x.get("status", "open"), assignee=x.get("assignee"),
                     priority=_priority(x)) for x in source]

def blocked(tracker: BrTracker) -> list[dict]:
    """The blocked set — the population `ready` cannot see BY DEFINITION.

    A separate read on purpose (same reasoning as the bd original): `ready`
    excludes blocked items, so no filter over it can reach these.
    """
    r = tracker._bd("list", "--status", "blocked", "--json", "--limit", "0")
    if r.returncode != 0:
        raise RuntimeError(f"br list --status blocked failed: {r.stderr.strip()[:120]}")
    payload = json.loads(r.stdout) if r.stdout.strip() else {}
    return payload.get("issues", []) if isinstance(payload, dict) else payload


def deferred(tracker: BrTracker) -> list[dict]:
    """Every row a deferral sweep must judge — BOTH representations (aegis-boj8a2).

    The clbx2 cutover (aegis-vyc3aa) moved deferral from `status = 'deferred'` to
    a `defer_until` FIELD with status left `open`, and BOTH shapes are live:
    measured 2026-09-03, 115 rows still carry `status = deferred` and 2 `open`
    rows carry a defer_until. So this reads the deferred set AND the open set and
    hands both over; the caller keys off the field, never the status. Reading one
    shape would be blind to most of the board — the same migration-kills-READERS
    error the cutover already produced once (aegis-vyc3aa).
    """
    out: list[dict] = []
    for status in ("deferred", "open"):
        r = tracker._bd("list", "--status", status, "--json", "--limit", "0")
        if r.returncode != 0:
            raise RuntimeError(
                f"br list --status {status} failed: {r.stderr.strip()[:120]}")
        payload = json.loads(r.stdout) if r.stdout.strip() else {}
        out.extend(payload.get("issues", []) if isinstance(payload, dict)
                   else payload)
    return out


def show(tracker: BrTracker, bead_id: str) -> dict:
    """One full item including dependency rows.

    `list` carries only a dependency COUNT, which cannot tell an open blocker
    from a closed one — the detail read is what stops a count being used as a
    status classifier.
    """
    r = tracker._bd("show", bead_id, "--json")
    if r.returncode != 0:
        raise RuntimeError(f"br show {bead_id} failed: {r.stderr.strip()[:120]}")
    value = json.loads(r.stdout) if r.stdout.strip() else {}
    if isinstance(value, dict) and "issues" in value:
        value = value["issues"]
    return value[0] if isinstance(value, list) and value else value


def claim(tracker: BrTracker, bead_id: str) -> None:
    """Mark an item in_progress — the dispatcher's WRITE.

    This one is a write, and post-cutover that matters more than the reads
    beside it: a claim that still went through retired `bd` would resolve UP
    into the town store (aegis-qx43o) rather than failing usefully, so the
    tracker would disagree with the board about who holds what.
    """
    r = tracker._bd("update", bead_id, "--status", "in_progress")
    if r.returncode != 0:
        raise RuntimeError(f"br update {bead_id} failed: {r.stderr.strip()[:120]}")


def append_comment(tracker: BrTracker, bead_id: str, body: str) -> None:
    """Append a comment through br's file-safe comments subcommand."""
    path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as f:
            f.write(body)
            path = f.name
        r = tracker._bd_for(bead_id, "comments", "add", bead_id, "--file", path)
        if r.returncode != 0:
            raise RuntimeError(
                f"br comments add {bead_id} failed: {r.stderr.strip()[:160]}")
    finally:
        if path:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
