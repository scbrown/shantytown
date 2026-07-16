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
