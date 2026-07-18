"""beads — the first-class tracker.

Two functions. If this needs more, the tracker is driving the harness.

Deliberately shells out to `bd` rather than opening its own Dolt connection.
That is not laziness, it is the finding: `gt sling --dry-run` makes 63 sequential
Dolt connections during resolution and takes 51s, while `bd show` makes 3 and
takes 0.20s (aegis-eu3s). Connections predict latency. The cheapest correct thing
is to make ONE `bd` call per operation and let bd own its pool.

If a future version opens its own connection, the budget test is what will catch
it — count the connections, don't hold a stopwatch.
"""
from __future__ import annotations
import json
import re
import subprocess

from .protocols import WorkItem


class BeadsTracker:
    def __init__(self, repo: str | None = None, timeout: int = 30):
        self.repo = repo          # -C <dir>; None = cwd
        self.timeout = timeout

    def _bd(self, *args: str) -> subprocess.CompletedProcess:
        cmd = ["bd"]
        if self.repo:
            cmd += ["-C", self.repo]
        cmd += list(args)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)

    def get(self, item_id: str) -> WorkItem:
        r = self._bd("show", item_id, "--json")
        if r.returncode != 0:
            # exit 2 territory: could not tell vs does not exist. Say which.
            raise LookupError(f"bd show {item_id} failed: {r.stderr.strip()[:120]}")
        d = json.loads(r.stdout)
        if isinstance(d, list):
            d = d[0]
        return WorkItem(
            id=d.get("id", item_id),
            title=d.get("title", ""),
            status=d.get("status", "open"),
            assignee=d.get("assignee"),
        )

    def create(self, title: str, **fields) -> WorkItem:
        """`bd create`. Returns the item, because the caller needs the new id.

        bd prints the id on stdout; we parse it rather than re-query, so create
        costs one bd invocation and not two. bd update is ~3.9s against bd show's
        0.18s (aegis-s9m7) — every avoided round trip is real money here.
        """
        args = ["create", title, "--json"]
        for k, v in fields.items():
            if v is None:
                continue
            args.append(f"--{k.replace('_', '-')}={v}")
        r = self._bd(*args)
        if r.returncode != 0:
            raise RuntimeError(f"bd create failed: {r.stderr.strip()[:120]}")
        try:
            d = json.loads(r.stdout)
            if isinstance(d, list):
                d = d[0]
            item_id = d.get("id", "")
        except json.JSONDecodeError:
            # bd's human output: "✓ Created issue: aegis-x1y2 — title"
            m = re.search(r"\b([a-z][a-z0-9_]*-[a-z0-9]+)\b", r.stdout)
            item_id = m.group(1) if m else ""
        if not item_id:
            # Never invent an id. A create that cannot name what it made did not
            # create anything the caller can use.
            raise RuntimeError(f"bd create gave no id: {r.stdout.strip()[:120]}")
        return WorkItem(id=item_id, title=title, status="open", assignee=fields.get("assignee"))

    def update(self, item_id: str, **fields) -> None:
        args = ["update", item_id]
        for k, v in fields.items():
            if v is None:
                continue
            args.append(f"--{k.replace('_', '-')}={v}")
        r = self._bd(*args)
        if r.returncode != 0:
            raise RuntimeError(f"bd update {item_id} failed: {r.stderr.strip()[:120]}")


# Plate precedence, shared verbatim with files.plate so the two backends order a
# plate identically (the two-implementation equivalence, aegis-260i). "In-hand"
# work outranks "not-started"; anything not listed (open, etc.) sorts last, then id.
_PLATE_RANK = {"hooked": 0, "in_progress": 1}


def plate(tracker: "BeadsTracker", agent: str) -> "WorkItem | None":
    """The ONE thing on an agent's plate, or None. A module function, not a method.

    Pays the debt files.plate() names: prime against the beads tracker used to
    show an empty plate because only the files backend had a reader. Now both do.

    THE RULING (aegis-gqr8, arnold): "what's on my plate" is NOT a third Tracker
    method — the two-function Tracker (get/update) is load-bearing; ellie's test
    and the BeadsTracker swap both depend on it, and malcolm's mine() broke both.
    It is a per-backend PLATE READER, injected into prime. malcolm's files
    implementation was the right pattern; this is its beads sibling.

    Returns AT MOST ONE item, by construction — cli.md: "one item, or none; a
    primer that prints a backlog is a dashboard." A function that cannot return
    two things cannot grow into a query API, which is what keeps the tracker from
    driving the harness. Ties broken deterministically (hooked before in_progress,
    then by id) so two runs agree.
    """
    import json
    r = tracker._bd("list", "--json")
    if r.returncode != 0:
        # could-not-look, not empty-plate. Raise so prime surfaces exit 2 rather
        # than reporting "nothing on your plate" when it simply could not ask.
        raise RuntimeError(f"bd list failed: {r.stderr.strip()[:120]}")
    rows = json.loads(r.stdout) if r.stdout.strip() else []
    mine = [
        x for x in rows
        if x.get("assignee") in (agent, agent.split("/")[-1])
        and x.get("status") != "closed"
    ]
    if not mine:
        return None
    # OPEN-ASSIGNED belongs on the plate (aegis-260i, malcolm): returning None
    # while 3 beads are assigned to you reports "nothing" when it means "nothing
    # STARTED" — the same silent-degradation class this reader is built to refuse.
    # This used to filter to hooked/in_progress and DIVERGED from files.plate's
    # "not closed"; the two-implementation rule exists to catch exactly that. Now
    # both include not-closed, with one shared precedence: in-hand outranks
    # not-started, then lowest id (deterministic across runs and across backends).
    mine.sort(key=lambda x: (_PLATE_RANK.get(x.get("status"), 2), x.get("id", "")))
    top = mine[0]
    return WorkItem(
        id=top.get("id", ""),
        title=top.get("title", ""),
        status=top.get("status", "open"),
        assignee=top.get("assignee"),
    )
