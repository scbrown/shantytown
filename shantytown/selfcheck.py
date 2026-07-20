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
built on it could never fail. Two things are compared, and the second replaced a
first attempt that was itself dead code (see check_self):

  1. the pipx-RECORDED SOURCE PATH vs the canonical checkout, and
  2. the INSTALLED BYTES vs that checkout's files.

(2) is the one that catches the everyday failure: `git pull` does not deploy `st`,
because it is installed non-editable, so the checkout moves forward and the running
code silently does not.
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


def check_self(*, run=_default_run, canonical: str = CANONICAL_SOURCE,
               stale_files=None) -> SelfHealth:
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

    head = _head(canonical_path, run)

    # IS THE CANONICAL CHECKOUT ITSELF CLEAN? Checked BEFORE staleness, because a
    # dirty checkout makes the staleness answer actively dangerous.
    #
    # `/home/braino/gt/shantytown` is a SHARED working copy — any crew member can
    # leave uncommitted work in it, and one had five modified files there while I
    # was writing this. So "always deploy from the canonical checkout" — the rule
    # dearing and I converged on after the private-worktree incident — does NOT by
    # itself stop you shipping someone's half-finished work. It only guarantees
    # the PATH is right, not that the TREE is.
    #
    # Worse, without this the module's own advice becomes the hazard: a dirty tree
    # reads as "stale install", and the fix it prints (`pipx install --force`)
    # would deploy that WIP to every agent. A check must not recommend the
    # incident it exists to prevent.
    dirty = _dirty_files(canonical_path, run)
    if dirty is None:
        return SelfHealth(CANNOT_TELL,
                          "could not determine whether the canonical checkout is "
                          "clean — cannot tell whether it is safe to deploy from",
                          recorded_source=recorded, installed_head=head,
                          canonical_head=head)
    if dirty:
        shown = ", ".join(sorted(dirty)[:3])
        more = f" (+{len(dirty) - 3} more)" if len(dirty) > 3 else ""
        return SelfHealth(
            BROKEN,
            f"the canonical checkout {canonical_path} has UNCOMMITTED changes in "
            f"{len(dirty)} file(s): {shown}{more}. It is a SHARED working copy, so "
            f"this is probably another agent's work in progress. Do NOT deploy "
            f"from it — `pipx install --force` would ship that WIP to every agent. "
            f"Deploy after it is committed and pushed.",
            recorded_source=recorded, installed_head=head, canonical_head=head)

    # COMPARE THE INSTALLED BYTES TO THE CHECKOUT, not one HEAD to another.
    #
    # The first version of this compared _head(recorded) with _head(canonical) —
    # and once the path check above has passed, those are THE SAME DIRECTORY, so
    # they were always equal and the mismatch branch was UNREACHABLE in
    # production. It passed its test only because the fake returned two different
    # values for one path. A branch that cannot fire is exactly the defect this
    # module exists to catch, shipped inside the module that catches it.
    #
    # The failure that actually happens is: someone pulls the checkout forward
    # and does not reinstall. The recorded path is still canonical and HEAD is
    # whatever you just pulled, so every path-and-HEAD check says green while the
    # RUNNING code is old. Only the installed files can answer it.
    stale = (stale_files or _stale_files)(canonical_path)
    if stale is None:
        return SelfHealth(CANNOT_TELL,
                          "could not compare the installed files against the "
                          "checkout — cannot tell whether this `st` is current",
                          recorded_source=recorded, installed_head=head,
                          canonical_head=head)
    if stale:
        shown = ", ".join(sorted(stale)[:3])
        more = f" (+{len(stale) - 3} more)" if len(stale) > 3 else ""
        return SelfHealth(
            BROKEN,
            f"the INSTALLED `st` differs from the checkout in {len(stale)} "
            f"file(s): {shown}{more}. The checkout is at {(head or '?')[:8]}; the "
            f"running code is older. A `git pull` does NOT deploy `st` — it is "
            f"installed non-editable. Fix: pipx install --force {canonical_path}",
            recorded_source=recorded, installed_head=head, canonical_head=head)

    return SelfHealth(OK, "", recorded_source=recorded,
                      installed_head=head, canonical_head=head)


def _dirty_files(canonical: str, run) -> set[str] | None:
    """Tracked files under the package dir with uncommitted modifications.

    Scoped to `shantytown/` deliberately: a modified README or a stray note in
    docs/ does not change what gets installed, and a check that fires on those
    would be noise — and noise is how a check gets ignored. Untracked files are
    excluded for the same reason: `build/` and `*.egg-info/` are produced BY
    installing and would otherwise make every post-deploy run red.

    None = could not look (caller renders cannot-tell, never a pass).
    """
    rc, out = run(("git", "-C", canonical, "status", "--porcelain", "--", "shantytown"))
    if rc != 0:
        return None
    dirty: set[str] = set()
    for line in out.splitlines():
        if len(line) < 4:
            continue
        status, name = line[:2], line[3:].strip()
        if status.startswith("??"):
            continue
        dirty.add(name.split("/")[-1])
    return dirty


def _installed_package_dir(venvs_root: str | None = None) -> Path | None:
    """The PIPX-INSTALLED package dir — deliberately NOT the running module.

    The first version asked the running module where it lived, which made the
    answer depend on HOW doctor was invoked: run it from a worktree during
    development and it compared that worktree against the canonical checkout and
    reported the fleet's install BROKEN, which is both wrong and the kind of
    false alarm that gets a check switched off in a day.

    doctor's question is about the DEPLOYED `st`, so resolve pipx's own layout
    and audit that, whatever is currently executing. Unresolvable = None =
    cannot-tell, never a pass.
    """
    root = Path(venvs_root) if venvs_root else \
        Path.home() / ".local/share/pipx/venvs"
    try:
        hits = sorted(root.glob("shantytown/lib/python*/site-packages/shantytown"))
    except OSError:
        return None
    for h in hits:
        if h.is_dir():
            return h
    return None


def _stale_files(canonical: str, venvs_root: str | None = None) -> set[str] | None:
    """Names of .py files whose INSTALLED bytes differ from the checkout's.

    Empty set = the running code matches the source tree. None = could not look
    (and per requirement 2 the caller renders that as cannot-tell, never a pass).

    Compares only the package's own .py files: that is what pipx copied and what
    actually executes. A file present in the checkout but missing from the
    install counts as stale — that is a module added since the last deploy.
    """
    installed = _installed_package_dir(venvs_root)
    src = Path(canonical) / "shantytown"
    if installed is None or not src.is_dir():
        return None
    # If we are RUNNING from the checkout itself (a dev invocation, or an
    # editable install), there is nothing to compare and nothing to be stale.
    try:
        if installed.samefile(src):
            return set()
    except OSError:
        return None
    stale: set[str] = set()
    try:
        for f in src.glob("*.py"):
            other = installed / f.name
            if not other.is_file() or other.read_bytes() != f.read_bytes():
                stale.add(f.name)
    except OSError:
        return None
    return stale


def render(h: SelfHealth) -> str:
    """One row, in doctor's voice."""
    if h.verdict == OK:
        # "matches" not "installed @": the sha is the CHECKOUT's HEAD, and the
        # claim being made is that the installed files equal that tree — not
        # that the install recorded a commit, which nothing here can know.
        head = (h.installed_head or "?")[:8]
        return (f"  • st       {h.recorded_source} @ {head} "
                f"— installed files match")
    mark = "✗" if h.verdict == BROKEN else "?"
    return f"  {mark} st       {h.note}"
