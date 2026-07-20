"""selfcheck — `st doctor` asking the question about ITSELF (aegis-daoh, q9eh).

doctor reports installed-vs-available for beads, bobbin, quipu and reactor, and
has never once asked it about `st`. The tool that audits deployment drift was the
only tool exempt from the audit — and it is the one whose staleness silently
corrupts every other answer it gives, because a stale `st doctor` reports a stale
world with total confidence.

THE INCIDENT (2026-07-20). `st` is pipx-installed NON-EDITABLE, so the venv holds a
COPY and the recorded source path decides what a rebuild rebuilds:

  * dearing deployed with `pipx install --force /home/braino/gt/shantytown-wt/dearing`
    — their own worktree. That silently RE-POINTED the fleet's recorded source at a
    private directory carrying untracked build/ and egg-info, on a commit that was
    not main.
  * arnold then ran `pipx reinstall shantytown`, which did exactly what it promises:
    faithfully rebuilt THE RECORDED SOURCE. The shared checkout had been pulled and
    was irrelevant.
  * Both of us then invented a mechanism ("pipx reused a cached wheel"). Both wrong.
    A controlled experiment refuted it and the pipx log named the real cause in one
    line. Nobody had read the tool.

Nothing in the system could have told anyone. `st --version` is permanently 0.0.1,
so a stale install NEVER looks stale, and the recorded source is not surfaced
anywhere. Hence this module, and dearing's two requirements, which are the contract:

  1. A recorded source that is not the CANONICAL checkout is an ERROR, not a note.
     That is the condition dearing created, and it must be impossible to hold
     without the tool shouting.
  2. It must FAIL TOWARD "CANNOT TELL" (like stop_event._lead_is_up). If it cannot
     read its own metadata, that is not a pass.

Deliberately NOT a version comparison: the version string cannot move, so a check
built on it could never fail. This compares the two things that DO move — the
recorded source PATH, and the git HEAD of that source against the canonical
checkout's HEAD.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

# The one true source for a fleet deploy. A recorded source anywhere else means
# somebody deployed from a directory only they can see.
CANONICAL_SOURCE = "/home/braino/gt/shantytown"

# Verdicts. Same three-outcome vocabulary as roles.check, on purpose: ok / broken /
# cannot tell. A checker that can only report health is not a checker.
OK, BROKEN, CANNOT_TELL = "ok", "broken", "cannot tell"


@dataclass(frozen=True)
class SelfHealth:
    verdict: str
    note: str = ""
    recorded_source: str | None = None
    installed_head: str | None = None
    canonical_head: str | None = None


def _pipx_metadata(run) -> dict | None:
    rc, out = run(("pipx", "list", "--json"))
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def _recorded_source(meta: dict, package: str = "shantytown") -> str | None:
    try:
        return meta["venvs"][package]["metadata"]["main_package"]["package_or_url"]
    except (KeyError, TypeError):
        return None


def _head(path: str, run) -> str | None:
    rc, out = run(("git", "-C", path, "rev-parse", "HEAD"))
    if rc != 0:
        return None
    h = out.strip()
    return h or None


def _default_run(argv: tuple[str, ...]) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def check_self(*, run=_default_run, canonical: str = CANONICAL_SOURCE) -> SelfHealth:
    """Is the `st` you are running built from the canonical checkout, at its HEAD?

    Every failure to LOOK is CANNOT_TELL, never OK — requirement 2. The one thing
    that is never inferred is health: this returns OK only when it has positively
    read a recorded source, confirmed it is the canonical path, read both HEADs,
    and found them equal.
    """
    meta = _pipx_metadata(run)
    if meta is None:
        return SelfHealth(CANNOT_TELL,
                          "could not read pipx metadata — cannot tell what source "
                          "this `st` was built from")

    recorded = _recorded_source(meta)
    if recorded is None:
        return SelfHealth(CANNOT_TELL,
                          "pipx has no recorded source for 'shantytown' — cannot "
                          "tell what this `st` was built from")

    canonical_path = str(Path(canonical))
    if str(Path(recorded)) != canonical_path:
        # Requirement 1: an ERROR, not a note. This is the condition where the
        # fleet's harness is a build of somebody's private tree.
        return SelfHealth(
            BROKEN,
            f"`st` was installed from {recorded!r}, NOT the canonical checkout "
            f"{canonical_path!r}. Whatever is in that directory — including "
            f"uncommitted work — is what the whole fleet is running, and a "
            f"`pipx reinstall` will faithfully rebuild it. "
            f"Fix: pipx install --force {canonical_path}",
            recorded_source=recorded)

    installed_head = _head(recorded, run)
    canonical_head = _head(canonical_path, run)
    if installed_head is None or canonical_head is None:
        return SelfHealth(CANNOT_TELL,
                          "could not read git HEAD for the source checkout — "
                          "cannot tell whether this `st` is current",
                          recorded_source=recorded,
                          installed_head=installed_head,
                          canonical_head=canonical_head)

    # NOTE the honest boundary, stated so nobody over-reads a green row: this
    # compares the checkout's HEAD to itself once the path matches, which catches
    # a source pointing at a different tree. It does NOT prove the installed
    # BYTES match that HEAD — pipx copied them at install time, and the checkout
    # can be pulled forward afterwards without reinstalling. Detecting that needs
    # a build stamp; until then this is a floor, not a guarantee, and calling it
    # a guarantee would be the same over-claim this module exists to stop.
    if installed_head != canonical_head:
        return SelfHealth(
            BROKEN,
            f"the source `st` was installed from is at {installed_head[:8]} but "
            f"the canonical checkout is at {canonical_head[:8]}",
            recorded_source=recorded,
            installed_head=installed_head,
            canonical_head=canonical_head)

    return SelfHealth(OK, "", recorded_source=recorded,
                      installed_head=installed_head,
                      canonical_head=canonical_head)


def render(h: SelfHealth) -> str:
    """One row, in doctor's voice."""
    if h.verdict == OK:
        return f"  • st       installed from {h.recorded_source} @ {h.installed_head[:8]}"
    mark = "✗" if h.verdict == BROKEN else "?"
    return f"  {mark} st       {h.note}"
