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

    def update(self, item_id: str, **fields) -> None:
        args = ["update", item_id]
        for k, v in fields.items():
            if v is None:
                continue
            args.append(f"--{k.replace('_', '-')}={v}")
        r = self._bd(*args)
        if r.returncode != 0:
            raise RuntimeError(f"bd update {item_id} failed: {r.stderr.strip()[:120]}")
