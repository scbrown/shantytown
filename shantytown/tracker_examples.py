"""Command examples pinned to the tracker that served the work.

Examples are advice, not tracker operations. Unknown routing must not turn
into a command against a default or retired board.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import shlex
import subprocess


@dataclass(frozen=True)
class TrackerExamples:
    argv: tuple[str, ...] = ()
    cwd: str | None = None
    diagnostic: str = "Tracker routing is unproven; verify it before changing work."

    def command(self, *args: str) -> str:
        if not self.argv:
            return self.diagnostic
        cmd = shlex.join((*self.argv, *args))
        return f"(cd {shlex.quote(self.cwd)} && {cmd})" if self.cwd else cmd

    def show(self, item: str) -> str:
        return self.command("show", item)

    def close(self, item: str) -> str:
        return self.command("close", item, "--reason", "<what landed and proof>")

    def unassign(self, item: str) -> str:
        return self.command("update", item, "--assignee", "")


def for_tracker(tracker) -> TrackerExamples:
    from .br import BrTracker
    from .sd import SdTracker
    if isinstance(tracker, SdTracker):
        try:
            # Same transport/location as the reader, not a second board resolver.
            loc = tracker._location()
            result = subprocess.run(
                [tracker._bin, *loc, "where", "--json"], cwd=tracker.repo,
                capture_output=True, text=True, timeout=tracker.timeout)
            if result.returncode:
                raise ValueError("where refused")
            route = json.loads(result.stdout)
            if not tracker.prefix or route.get("prefix") != tracker.prefix:
                raise ValueError("prefix mismatch")
            if tracker.quipu:
                from urllib.parse import urlsplit
                endpoint = urlsplit(tracker.quipu)
                graph = route.get("graph")
                if (endpoint.scheme not in ("http", "https") or not endpoint.netloc
                        or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
                        or not isinstance(graph, str) or not graph.startswith(("http://", "https://"))):
                    raise ValueError("unsafe route")
                if ((route.get("quipu_url") or "").rstrip("/") != tracker.quipu.rstrip("/")
                        or route.get("mode") != "remote" or not route.get("graph")):
                    raise ValueError("remote route mismatch")
                return TrackerExamples((tracker._bin, "--quipu", tracker.quipu,
                                        "--graph", route["graph"]))
            from pathlib import Path
            if (not tracker.store or not Path(tracker.store).is_file()
                    or route.get("database_path") != str(Path(tracker.store).absolute())
                    or route.get("mode") != "local"):
                raise ValueError("local route mismatch")
            return TrackerExamples((tracker._bin, "--store", str(Path(tracker.store).absolute())))
        except Exception:
            # Do not echo transport errors: wrappers can include auth values.
            return TrackerExamples(diagnostic="Seeds routing is unproven; verify sd where before changing work.")
    if isinstance(tracker, BrTracker) and tracker.repo and not tracker.extra_repos:
        # A repo-less br command resolves relative to the agent's ambient cwd.
        from pathlib import Path
        import os
        return TrackerExamples((os.environ.get("SHANTY_BR_BIN", "br"),),
                               cwd=str(Path(tracker.repo).absolute()))
    return TrackerExamples()


def for_deployment(root, reg=None) -> TrackerExamples:
    try:
        from .feed_check import _br_tracker
        return for_tracker(_br_tracker(root, reg)) if root is not None else TrackerExamples()
    except Exception:
        return TrackerExamples()
