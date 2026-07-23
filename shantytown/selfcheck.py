"""selfcheck — `st doctor` asking the question about ITSELF (internal-ref, q9eh).

doctor reports installed-vs-available for beads, bobbin, quipu and reactor, and
has never once asked it about `st`. The tool that audits deployment drift was the
only tool exempt from the audit — and it is the one whose staleness silently
corrupts every other answer it gives, because a stale `st doctor` reports a stale
world with total confidence.

THE INCIDENT (2026-07-20). `st` is pipx-installed NON-EDITABLE, so the venv holds a
COPY and the recorded source path decides what a rebuild rebuilds:

  * dearing deployed with `pipx install --force <their-own-worktree>`
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

THE EDITABLE ERA (internal-ref, 2026-07-20 22:15). `st` is now `pipx install -e` —
option (3) on that bead, the one that makes the pipx gap structurally impossible:
there are no installed bytes to go stale, because the checkout IS the install.
This module had to learn that, because it predated it and answered CANNOT_TELL
for the healthiest install this host has ever had (site-packages carries only a
PEP 660 finder, so _installed_package_dir found nothing to compare). Under
editable, two sentences above INVERT, and the check moves with them:

  * "git pull does not deploy" is now FALSE — a pull IS the deploy. So the drift
    that matters is the CHECKOUT vs ITS UPSTREAM, and check_self now fetches and
    counts it (the `remote` flag): a fix landed on main that nobody pulled is
    this bead's original defect, one layer up, and it reads green without this.
  * a dirty shared checkout is no longer "would deploy WIP" — the WIP is ALREADY
    RUNNING in every `st` on the host, live, uncommitted.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# The one true source for a fleet deploy. A recorded source anywhere else means
# somebody deployed from a directory only they can see.
#
# NOT hardcoded to one operator's home. It was — and that was two bugs wearing one
# coat: it published the operator's account name and private tree layout from a
# PUBLIC repo, and it meant this check could only ever pass on one machine, so
# anybody else installing `st` got a permanent false "broken" verdict for their
# correct install. A deployment checker that is wrong everywhere except one laptop
# is not a checker.
#
# Resolution order, first hit wins:
#   1. $SHANTY_CANONICAL_SOURCE  — explicit, and how a fleet pins it
#   2. the git top-level of the running package, if it is in a checkout
#   3. None — which makes the verdict CANNOT_TELL, never OK. "I do not know where
#      canonical is" is not "you are fine"; that is the whole doctrine of this
#      module applied to its own configuration.
_CANONICAL_ENV = "SHANTY_CANONICAL_SOURCE"


def canonical_source(run=None) -> str | None:
    """Where a fleet deploy is supposed to be built FROM. None = unknown."""
    import os
    env = os.environ.get(_CANONICAL_ENV)
    if env:
        return env.rstrip("/")
    runner = run or _default_run
    rc, out = runner(("git", "-C", str(Path(__file__).resolve().parent),
                      "rev-parse", "--show-toplevel"))
    return out.strip().rstrip("/") if rc == 0 and out.strip() else None

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
    # Editable install: the checkout IS the install, so "installed files match"
    # is true BY CONSTRUCTION and the render says so instead of implying a copy
    # was compared.
    editable: bool = False
    # Commits the checkout is behind its upstream, or None = not checked / could
    # not check. None is NOT zero: "current with the remote" is a measurement,
    # and the render shows the gap ("remote unchecked") rather than implying it.
    behind: int | None = None
    upstream: str | None = None


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


def check_self(*, run=_default_run, canonical: str | None = None,
               stale_files=None, editable_target=None,
               remote: bool = True) -> SelfHealth:
    """Is the `st` you are running built from the canonical checkout, at its HEAD?

    Every failure to LOOK is CANNOT_TELL, never OK — requirement 2. The one thing
    that is never inferred is health: this returns OK only when it has positively
    read a recorded source, confirmed it is the canonical path, read both HEADs,
    and found them equal.

    `remote=False` skips the behind-upstream fetch (doctor's --no-latest, same
    semantics: no network). The render then says "remote unchecked" — skipping a
    measurement is allowed, implying one is not.
    """
    if canonical is None:
        canonical = canonical_source(run)
    if canonical is None:
        return SelfHealth(CANNOT_TELL,
                          f"no canonical source is configured and this package is "
                          f"not in a git checkout — set ${_CANONICAL_ENV} to the "
                          f"checkout a fleet deploy must be built from")

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

    # EDITABLE? Resolved once, here, because two messages below must tell the
    # truth differently for it: a dirty checkout is not "would deploy WIP", it
    # is WIP already running; and "installed files match" is by construction,
    # not by comparison.
    target = (editable_target or _editable_target)()
    editable = False
    if target is not None:
        try:
            editable = target.samefile(Path(canonical_path) / "shantytown")
        except OSError:
            editable = False

    # IS THE CANONICAL CHECKOUT ITSELF CLEAN? Checked BEFORE staleness, because a
    # dirty checkout makes the staleness answer actively dangerous.
    #
    # The canonical checkout is a SHARED working copy — any crew member can
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
        if editable:
            # Sharper than the non-editable case, and it must say so: there is
            # no "would". The checkout IS the install, so this WIP is what every
            # `st` on the host is executing right now.
            why = (f"the canonical checkout {canonical_path} has UNCOMMITTED "
                   f"changes in {len(dirty)} file(s): {shown}{more} — and `st` "
                   f"is installed EDITABLE from it, so that WIP is ALREADY "
                   f"RUNNING in every `st` on this host, live and uncommitted. "
                   f"Commit-and-push it or revert it; there is no deploy step "
                   f"between the edit and the fleet.")
        else:
            why = (f"the canonical checkout {canonical_path} has UNCOMMITTED "
                   f"changes in {len(dirty)} file(s): {shown}{more}. It is a "
                   f"SHARED working copy, so this is probably another agent's "
                   f"work in progress. Do NOT deploy from it — `pipx install "
                   f"--force` would ship that WIP to every agent. Deploy after "
                   f"it is committed and pushed.")
        return SelfHealth(
            BROKEN, why, recorded_source=recorded, installed_head=head,
            canonical_head=head, editable=editable)

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
                          canonical_head=head, editable=editable)
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

    # THE DRIFT THAT REMAINS once the install matches the checkout: is the
    # CHECKOUT itself current? This is internal-ref's original defect — a fix on
    # both remotes that no operator is running — which every check above waves
    # through, because the install faithfully matches a stale tree. Under the
    # editable install this is the ONLY drift left, and a `git pull` is the
    # whole deploy.
    behind = _behind_upstream(canonical_path, run) if remote else None
    if behind is not None and behind[0] > 0:
        n, upstream = behind
        fix = (f"git -C {canonical_path} pull --ff-only  (the checkout IS the "
               f"install — the pull deploys it)" if editable else
               f"git -C {canonical_path} pull --ff-only && "
               f"pipx install --force {canonical_path}")
        return SelfHealth(
            BROKEN,
            f"the canonical checkout is {n} commit(s) BEHIND {upstream} — "
            f"work landed on the remote that no `st` on this host is running. "
            f"This install matches the checkout, and the checkout is stale, "
            f"which reads green on every other row. Fix: {fix}",
            recorded_source=recorded, installed_head=head, canonical_head=head,
            editable=editable, behind=n, upstream=upstream)

    return SelfHealth(OK, "", recorded_source=recorded,
                      installed_head=head, canonical_head=head,
                      editable=editable,
                      behind=None if behind is None else behind[0],
                      upstream=None if behind is None else behind[1])


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


def _editable_target(venvs_root: str | None = None) -> Path | None:
    """Where the EDITABLE install actually points, or None if not editable.

    A PEP 660 editable install leaves NO copied package dir in site-packages —
    only a generated finder module mapping the package name to a source path.
    That absence is what made the old check answer CANNOT_TELL for a perfectly
    healthy editable install: it went looking for bytes that do not exist. The
    finder is the artifact that DOES exist, so read the mapping out of it.

    None = not an editable install (or could not look) — the caller falls back
    to the copied-bytes comparison, which is the right question for that mode.
    """
    root = Path(venvs_root) if venvs_root else \
        Path.home() / ".local/share/pipx/venvs"
    try:
        sps = sorted(root.glob("shantytown/lib/python*/site-packages"))
    except OSError:
        return None
    for sp in sps:
        for f in sorted(sp.glob("__editable__*shantytown*.py")):
            try:
                text = f.read_text()
            except OSError:
                continue
            # The finder carries MAPPING = {'shantytown': '<abs path>/shantytown'}
            # — measured on the live install. Match the mapped path, not the
            # module layout around it.
            m = re.search(r"['\"](/[^'\"]+?/shantytown)['\"]", text)
            if m:
                return Path(m.group(1))
    return None


def _behind_upstream(canonical: str, run) -> tuple[int, str] | None:
    """(commits behind, upstream ref) for the canonical checkout, or None.

    THE QUESTION THAT MOVED (internal-ref). Under a non-editable install the drift
    lived between the checkout and the venv copy; under editable there is no
    copy, so the same defect — a fix landed on main that no operator is running
    — now lives between the checkout and its REMOTE. This bead's own history has
    the sharpest form: the shared checkout once tracked a remote whose ref was
    unfetched, so `git pull` said "Already up to date" while sitting a commit
    behind. Hence FETCH FIRST, then count — asking the local ref without
    fetching is asking the instrument that already lied.

    The upstream is the checked-out branch's OWN @{u}, not a hardcoded
    "origin/main": this checkout has tracked different remotes at different
    times, and the drift that matters is against whatever a `git pull` here
    would actually consult.

    None = could not tell (no upstream configured, fetch failed, unparsable
    count). Rendered as "remote unchecked", never as current.
    """
    rc, out = run(("git", "-C", canonical, "rev-parse", "--abbrev-ref",
                   "--symbolic-full-name", "@{u}"))
    upstream = out.strip().splitlines()[-1] if rc == 0 and out.strip() else ""
    if not upstream or "/" not in upstream:
        return None
    rc, _ = run(("git", "-C", canonical, "fetch", "--quiet",
                 upstream.split("/", 1)[0]))
    if rc != 0:
        return None
    rc, out = run(("git", "-C", canonical, "rev-list", "--count",
                   f"HEAD..{upstream}"))
    n = out.strip()
    if rc != 0 or not n.isdigit():
        return None
    return int(n), upstream


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
    if not src.is_dir():
        return None
    if installed is None:
        # No copied package dir is exactly what an EDITABLE install looks like
        # (PEP 660: site-packages carries only a finder). If the finder points
        # at the canonical package dir, the running code equals the checkout BY
        # CONSTRUCTION — nothing exists to be stale. A finder pointing anywhere
        # else, or no finder at all, stays None: could not compare is not
        # current.
        target = _editable_target(venvs_root)
        if target is not None:
            try:
                if target.samefile(src):
                    return set()
            except OSError:
                return None
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
        what = ("editable — the checkout IS the install" if h.editable
                else "installed files match")
        # The remote claim is stated exactly as strongly as it was measured:
        # counted-current names the upstream; unmeasured says so. Silence here
        # would read as "current everywhere", which is the lie this module
        # exists to stop telling.
        tail = (f", current with {h.upstream}" if h.behind == 0 and h.upstream
                else ", remote unchecked")
        return f"  • st       {h.recorded_source} @ {head} — {what}{tail}"
    mark = "✗" if h.verdict == BROKEN else "?"
    return f"  {mark} st       {h.note}"
