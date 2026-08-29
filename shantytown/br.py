"""br — SQLite+JSONL tracker backend, beside the legacy bd adapter."""
from __future__ import annotations

import json
import os
import subprocess

from .beads import BeadsTracker, _PLATE_RANK, _priority
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


def rows(tracker: BrTracker) -> list[dict]:
    """Every issue in every configured br store, refusing partial unions."""
    out: list[dict] = []
    repos = tracker.repos or [None]
    for repo in repos:
        r = tracker._bd_in(repo, "list", "--json", "--limit", "0")
        if r.returncode != 0:
            raise RuntimeError(
                f"br list failed for store {repo or '(default)'}: "
                f"{r.stderr.strip()[:120]}")
        payload = json.loads(r.stdout) if r.stdout.strip() else {}
        out.extend(payload.get("issues", []) if isinstance(payload, dict) else payload)
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


def plate(tracker: BrTracker, agent: str) -> WorkItem | None:
    mine = [
        row for row in rows(tracker)
        if row.get("assignee") in (agent, agent.split("/")[-1])
        and row.get("status") != "closed"
        and not is_message(row.get("title", ""))
        and not is_unworkable(row.get("status"))
    ]
    if not mine:
        return None
    mine.sort(key=lambda row: (_PLATE_RANK.get(row.get("status"), 2),
                               row.get("id", "")))
    row = mine[0]
    return WorkItem(id=row.get("id", ""), title=row.get("title", ""),
                    status=row.get("status", "open"),
                    assignee=row.get("assignee"), priority=_priority(row))


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
