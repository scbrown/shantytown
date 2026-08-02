"""stale_guard — tell an agent its tree is behind AT THE MOMENT IT EDITS.

THE GAP THIS FILLS (aegis-ib65p decision 5, Stiwi: "we cannot leave it up to the
agent"). Dispatch-time refresh is necessary and NOT sufficient, for two reasons
that are both routine rather than exotic:

  1. TIME. An agent dispatched at 19:00 may not touch a file until 21:00, by
     which point main has moved eight times. The tree was current when it was
     handed over and is stale when it is used, and nothing between those two
     moments says so.
  2. NO DISPATCH AT ALL. An agent that picks its own next item off `bd ready`
     was never dispatched, so the dispatch-time refresh never ran for it. That
     is the documented, encouraged propulsion loop — not a corner case.

So the check has to fire where the ACT happens. This is the same argument that
put the shared-checkout guard at commit time rather than in a runbook: a rule
that depends on remembering is a rule that is measured to be forgotten (12 of 12
worktrees behind, one by 155 commits, on the repo the fleet changes hourly).

THREE CONSTRAINTS, each of which would sink it if violated:

  ADVISE, NEVER BLOCK. It prints and exits 0, always. An edit refused because a
  tree was behind would be a correctness rule enforced as an availability
  outage, and the first time it misfired somebody would remove it.

  NEVER AUTO-PULL. Explicit in the decision, and it is the important one.
  Rebasing under a live agent changes files out from under work in progress —
  the agent's next edit lands on content it never read, and its mental model and
  the disk silently diverge. That is a worse failure than staleness, and it is
  unrecoverable rather than merely wasteful. This module has no write path at
  all: it cannot pull even by accident.

  CHEAP. No fetch — `tree_staleness(fetch=False)` reads already-fetched refs, so
  the cost is two `rev-list --count` calls against local refs. A hook that
  reached the network on every edit would add latency to every keystroke-level
  action and would be switched off within a day, at which point it protects
  nothing. The honest cost of that choice is that this reports staleness "as of
  the last fetch" — it is a tripwire, not an oracle, and it says so.

THROTTLED PER TREE, because the failure mode of an advisory is not being wrong,
it is being CONSTANT. Repeating the same line on all forty edits in a task
teaches the reader to skip it, and then the one time it matters it is skipped
too. One notice per tree per window; a CHANGE in the finding re-arms it
immediately, because "now 3 behind" after "1 behind" is new information.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# How long a single tree stays quiet after it has been reported. Long enough to
# cover a working stretch, short enough that a tree going stale mid-task is
# still surfaced within one.
QUIET_SECONDS = 1800

STATE_DIR = ".shanty-stale-guard"


def _repo_root(path: Path) -> Path | None:
    """The git tree containing `path`, or None. Uses --show-toplevel so a linked
    WORKTREE resolves to the worktree, not the shared checkout — which is the
    whole point: the worktree is the tree the agent actually edits."""
    try:
        d = path if path.is_dir() else path.parent
        r = subprocess.run(["git", "-C", str(d), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return None
        out = (r.stdout or "").strip()
        return Path(out) if out else None
    except Exception:
        return None


def _state_path(repo: Path) -> Path:
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / STATE_DIR
    key = str(repo).replace("/", "_").strip("_")
    return base / f"{key}.json"


def _should_report(repo: Path, finding: str, now: float | None = None) -> bool:
    """Throttle by (tree, finding). A CHANGED finding always re-arms.

    Fails OPEN — an unreadable or unwritable state file means we report. The
    cost of a duplicate line is a duplicate line; the cost of a swallowed one is
    the thing this module exists to prevent.
    """
    now = time.time() if now is None else now
    p = _state_path(repo)
    try:
        prev = json.loads(p.read_text())
        if prev.get("finding") == finding and now - float(prev.get("at", 0)) < QUIET_SECONDS:
            return False
    except Exception:
        pass
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps({"finding": finding, "at": now}))
        os.replace(tmp, p)
    except Exception:
        pass
    return True


def advise(path: Path, now: float | None = None) -> str | None:
    """The advisory line for the tree containing `path`, or None. Pure-ish: it
    reads git and the throttle file, and writes nothing to the repo."""
    from .workspace import tree_staleness
    repo = _repo_root(Path(path))
    if repo is None:
        return None                     # not in a git tree — nothing to say
    s = tree_staleness(repo, fetch=False)
    # An UNRESOLVABLE upstream is not reported here. It is a real condition, but
    # it is a repo-configuration problem that every single edit would re-report
    # and that no agent mid-task can fix — the dispatch and `st crew` paths say
    # it in places where somebody can act. An advisory that fires constantly and
    # is never actionable is how the whole channel gets ignored.
    if s.error or s.current():
        return None
    bits = []
    if s.behind:
        bits.append(f"{s.behind} commit(s) BEHIND {s.ref}")
    if s.unpushed:
        bits.append(f"{s.unpushed} local commit(s) NOT pushed")
    finding = "|".join(bits)
    if not _should_report(repo, finding, now=now):
        return None
    msg = [f"⚠ stale tree: {repo} is {' and '.join(bits)} (as of the last fetch)."]
    if s.behind:
        msg.append("Someone may have already built what you are about to build — "
                   "`git -C %s log --oneline HEAD..%s` before you start, and check "
                   "open PRs (unmerged work will NOT show there)." % (repo, s.ref))
    if s.unpushed:
        msg.append("Those commits are on no remote ref this tree knows about — fetch to confirm, then push; if it is real they exist in exactly one place.")
    msg.append("NOT pulled for you: changing files under a live edit is a worse "
               "bug than staleness. Rebase yourself when your tree is clean.")
    return " ".join(msg)


def _edited_path(payload: dict) -> Path | None:
    ti = payload.get("tool_input") or {}
    for k in ("file_path", "notebook_path", "path"):
        if ti.get(k):
            return Path(str(ti[k]))
    return None


def main(argv: list[str] | None = None) -> int:
    """PreToolUse entry point. ALWAYS exits 0."""
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
        target = _edited_path(payload) if payload else None
        if target is None:
            argv = list(sys.argv[1:] if argv is None else argv)
            target = Path(argv[0]) if argv else Path.cwd()
        line = advise(target)
        if line:
            print(line)
    except Exception:
        # FAIL OPEN AND SILENT, for the reasons untracked.main states: none of
        # the failure modes here (git absent, a torn state file, a path outside
        # any repo) are things the agent can act on, and a traceback per tool
        # call is worse than no advisory at all.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
