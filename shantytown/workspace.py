"""workspace — ensure an agent's DIRECTORY exists before anything is launched.

The workspace leg of #5 (split out from the session work). #5's session half
rules the pane; this is the DISTINCT concern deliberately kept out of session
lifecycle: the cwd the launch command runs in. Analogous to `gt crew add`'s clone
step, NOT to session lifecycle.

    Panes owns the SESSION.  runtime.compose owns the STRING.  This owns the DIR.

THE CONTRACT (one function, three outcomes):

    present  -> return the path, touch nothing.        IDEMPOTENT.
    absent   -> clone it from the card's source, then return the path.
    can't    -> RAISE. Never return a path we did not verify.

REFUSE RATHER THAN LAUNCH. Before this existed, runtime.compose blindly prefixed
`cd {card.workspace} && ...`; if that directory did not exist the whole launch
chain broke DOWNSTREAM — inside a tmux pane, as a shell error, after the session
had already been created. There was no ensure step and no clean refusal. A
missing workspace must be caught HERE, before any tmux mutation, so the refusal
creates nothing (arnold's #5 rule: "write nothing, launch nothing").

WHERE IT IS CALLED, AND WHY NOT INSIDE compose():
The bead sketched "called by Runtime.start before compose delivers". It is called
by the CLI instead, between compose's refusals and the tmux mutation. compose()
is a PURE STRING BUILDER whose invariant is asserted on its own return value; a
clone inside it would make composing — including `st new --dry-run`, which
composes and prints — mutate the disk. Dry-run must create NOTHING, and that is
exactly the property design.md names as a must-have test. So: compose stays pure,
the CLI ensures.

NEVER TOUCHES AN EXISTING DIRECTORY. No fetch, no pull, no clean, no checkout. An
agent's workspace may hold uncommitted work (a crew clone always does), and a
launcher that "helpfully" syncs it is a launcher that eats work. We measured that:
concurrent operations on a shared checkout swallowed one agent's commit and
BOTH SIDES reported success. Present means present. That is the whole check.
"""
from __future__ import annotations
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .protocols import Agent


class WorkspaceError(RuntimeError):
    """The workspace could not be guaranteed. REFUSE: launch nothing.

    Same shape as CapabilityError/SettingsError, and for the same reason — the
    failure we do not ship is the silent one. An agent launched into a missing or
    wrong directory is worse than an agent that never launched: it comes up, it
    looks alive in `st crew`, and it reads someone else's CLAUDE.md or none at
    all. The loud refusal IS the feature.
    """


# Cloning is INJECTED so the whole contract is testable without a network, a
# remote, or git itself. The default is the real thing.
Cloner = Callable[[str, Path], None]


def git_clone(source: str, dest: Path) -> None:
    """The default cloner: `git clone <source> <dest>`. Raises on failure.

    Output is captured and folded into the exception rather than printed — a
    launcher that spews clone chatter into the operator's terminal buries the one
    line that matters. If it fails, the stderr comes back attached to the refusal.
    """
    r = subprocess.run(
        ["git", "clone", source, str(dest)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise WorkspaceError(
            f"git clone {source!r} -> {dest} failed (exit {r.returncode}): "
            f"{(r.stderr or r.stdout).strip()}"
        )


def normalize_source(url: str) -> str:
    """Reduce a git URL to something two spellings of the SAME repo agree on.

    Deliberately CONSERVATIVE. It strips only what is provably cosmetic — a
    trailing slash and a trailing `.git`, which git itself treats as the same
    remote — and lowercases nothing else. It does NOT try to equate
    ssh://git@host/x with https://host/x: those CAN be the same repo, but they
    can also be a fork on a different host, and this function's answer decides
    whether we refuse a launch. An over-eager normalizer would hand back the
    silent adopt we are here to remove, just with extra steps.

    The cost of being conservative is a false refusal, which is loud, names both
    URLs, and a human fixes in one edit. The cost of being clever is launching an
    agent into the wrong tree, which nothing detects. Not a close call.
    """
    u = (url or "").strip()
    while u.endswith("/"):
        u = u[:-1]
    if u.endswith(".git"):
        u = u[:-4]
    return u


def git_origin(path: Path) -> str | None:
    """The `origin` remote of an existing tree, or None if there isn't one.

    None means CANNOT TELL — not a repo, no origin, or git unavailable. The
    caller keeps that distinct from a mismatch, because they warrant different
    sentences: "this is a clone of something else" and "I could not establish
    what this is" are different facts, and collapsing them is how a checker
    starts lying.
    """
    r = subprocess.run(
        ["git", "-C", str(path), "remote", "get-url", "origin"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


# Reading the origin is INJECTED for the same reason cloning is: the refusal has
# to be testable without a network or a real remote.
OriginReader = Callable[[Path], "str | None"]


def _refuse_wrong_tree(card: Agent, path: Path, origin: OriginReader) -> None:
    """RAISE if an existing workspace is not a clone of the card's source.

    Returns silently on a match. Two distinct failures, named separately on
    purpose — an operator fixes them differently:

      MISMATCH     the tree is a clone of a different remote. We can say exactly
                   what we expected and what we found, so we do.
      CANNOT TELL  no origin readable (not a repo, no remote). We refuse too, but
                   we do NOT claim it is the wrong repo, because we do not know
                   that. "There but WRONG is an error" covers this: a directory
                   we cannot identify is not a directory we have verified, and
                   the whole contract of this module is "never return a path we
                   did not verify".
    """
    found = origin(path)
    if found is None:
        raise WorkspaceError(
            f"workspace for {card.name} already exists at {path}, but it has no "
            f"readable git origin, so it CANNOT be confirmed as a clone of "
            f"{card.workspace_source!r}. Refusing to adopt an unverifiable tree. "
            f"Fix: point workspace at the right directory, clear workspace_source "
            f"if this tree is deliberately not a clone, or move {path} aside."
        )
    if normalize_source(found) != normalize_source(card.workspace_source):
        raise WorkspaceError(
            f"workspace for {card.name} at {path} is a clone of the WRONG repo. "
            f"expected: {card.workspace_source!r}  found: {found!r}. "
            f"Refusing to launch into it — an agent in the wrong tree looks "
            f"exactly like an agent in the right one. Fix: move {path} aside, or "
            f"correct workspace_source on the card."
        )


def ensure_workspace(card: Agent, clone: Cloner = git_clone,
                     origin: OriginReader = git_origin) -> str | None:
    """Guarantee card.workspace exists AND IS THE RIGHT TREE; return it as cwd.

    Returns None when the card elects no workspace — that is not a failure, it is
    "launch in the default cwd", which is what compose() already does when
    card.workspace is None. Nothing to ensure, nothing to refuse.
    """
    if not card.workspace:
        return None                      # no workspace elected — nothing to ensure

    path = Path(card.workspace).expanduser()

    if path.is_dir():
        # PRESENT IS NOT THE SAME AS CORRECT (internal-ref gap 2). This used to be a
        # bare `return str(path)` — idempotence read as "present -> leave it
        # alone". But dearing's rule is: already there AND CORRECT is success;
        # there but WRONG is an error, never a silent adopt. If the card names a
        # workspace_source and this tree is a clone of something ELSE, adopting it
        # launches the agent into the wrong repo — and a wrong workspace is
        # indistinguishable from a right one once the agent is up. That is the
        # SAME justification already written below for refusing a guessed remote;
        # it was simply never applied to the directory that already existed.
        if card.workspace_source:
            _refuse_wrong_tree(card, path, origin)
        # No workspace_source: nothing to check it AGAINST, and inventing a
        # expectation from a naming convention is the guessed-remote failure
        # again. Adopt, exactly as before.
        return str(path)

    if path.exists():
        # A file (or socket, or symlink to one) sitting where the workspace should
        # be. `cd` into it fails; cloning onto it would fail too. This is not a
        # missing workspace, it is a WRONG one, and it needs a human.
        raise WorkspaceError(
            f"workspace for {card.name} is not a directory: {path}. "
            f"Refusing to launch into it."
        )

    if not card.workspace_source:
        # The refusal the bead names: dir absent, and the card carries no clone
        # source, so there is nothing to create it FROM. We do not invent one from
        # a naming convention — a guessed remote is how an agent gets launched
        # into the wrong repo, and a wrong workspace is indistinguishable from a
        # right one once the agent is up.
        raise WorkspaceError(
            f"workspace for {card.name} does not exist: {path}, and the card "
            f"carries no workspace_source to clone it from. Refusing to launch "
            f"into a missing workspace. Fix: create the directory, or set "
            f"workspace_source on the card."
        )

    # Clone into a STAGING sibling and rename into place. A `git clone` that dies
    # partway (network drop, bad ref, disk full) leaves a partial tree behind; if
    # that tree were at the final path, the NEXT run would take the `is_dir()`
    # branch above and launch an agent into a broken half-clone — idempotence
    # turned into a trap. The rename is the atomic step, so the final path either
    # does not exist or is a completed clone. Never both.
    staging = path.parent / f".st-clone-{card.name}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if staging.exists():
        shutil.rmtree(staging)           # debris from an earlier crash
    try:
        clone(card.workspace_source, staging)
        if not staging.is_dir():
            # A cloner that reported success and produced nothing. Trusting it
            # would return a path that does not exist — the exact lie this
            # function exists to prevent.
            raise WorkspaceError(
                f"clone of {card.workspace_source!r} for {card.name} reported "
                f"success but produced no directory at {staging}. Refusing."
            )
        try:
            staging.rename(path)
        except OSError:
            # Lost a race: someone else materialized the workspace while we
            # cloned. Theirs wins (it may already be in use); ours is discarded.
            if not path.is_dir():
                raise
            shutil.rmtree(staging, ignore_errors=True)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    if not path.is_dir():
        raise WorkspaceError(
            f"workspace for {card.name} still does not exist after clone: {path}"
        )
    return str(path)


# ---------------------------------------------------------------------------
# Worktrees for SHARED PROJECT repos (internal-ref).
#
# ensure_workspace above owns the agent's OWN clone (crew/<name>): one writer,
# one tree. This owns the other case: a SHARED project repo — ~/gt/shantytown,
# ~/gt/quipu, ~/gt/hank, ~/gt/goldblum — that many agents touch at once. A shared
# checkout shares its index and HEAD with every process in it, so one agent's
# `git add`/`commit`/`reset` reaches into another's staging area and BOTH SIDES
# report success (measured: internal-ref/iaef — a commit left `git log` entirely and
# nothing errored). The fix is not a guard that refuses the commit; it is to give
# each agent its OWN worktree off the shared repo, so its index/HEAD are its own
# and the shared checkout is never the write surface for two agents.
#
# This used to be a MANUAL step: agents were told to run scripts/crew-worktree.sh
# / `git worktree add` by hand, and a reminder was put in crew CLAUDE.md. A manual
# discipline is the vigilance-not-mechanism failure this fleet keeps paying for
# (internal-ref). st provisions the worktree now; the agent never has to remember.
#
# Layout mirrors crew-worktree.sh so the two agree: <repo>-wt/<agent> on branch
# wt/<agent>. Both the add and the remove are INJECTED, same as cloning above, so
# the contract is testable without a real repo.

WorktreeAdd = Callable[[Path, Path, str, str], None]     # (shared, dest, agent, base)
WorktreeRemove = Callable[[Path, Path], None]            # (shared, dest)
WorktreeHoldsWork = Callable[[Path, str], bool]          # (dest, base) -> keep it?


def git_worktree_add(shared: Path, dest: Path, agent: str, base: str) -> None:
    """Default provisioner: add <dest> as a worktree of <shared> on wt/<agent>.

    Starts the branch off <base> (origin/main) so the agent begins current, not
    off whatever the shared checkout happens to have checked out. A failed fetch
    is a WARNING, not a refusal — offline or behind is survivable and the local
    refs still work; launching off nothing is not. If wt/<agent> already exists (a
    prior worktree was removed but the branch kept its commits) we reuse it rather
    than fail trying to re-create it.
    """
    branch = f"wt/{agent}"
    # Bring the shared checkout's refs current; tolerate failure (see docstring).
    subprocess.run(["git", "-C", str(shared), "fetch", "origin", "--quiet"],
                   capture_output=True, text=True)

    def _ref_exists(ref: str) -> bool:
        return subprocess.run(
            ["git", "-C", str(shared), "rev-parse", "--verify", "--quiet", ref],
            capture_output=True, text=True).returncode == 0

    dest.parent.mkdir(parents=True, exist_ok=True)
    if _ref_exists(f"refs/heads/{branch}"):
        cmd = ["git", "-C", str(shared), "worktree", "add", str(dest), branch]
    else:
        start = base if _ref_exists(base) else "HEAD"
        cmd = ["git", "-C", str(shared), "worktree", "add", "-b", branch,
               str(dest), start]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise WorkspaceError(
            f"git worktree add for {agent} off {shared} failed "
            f"(exit {r.returncode}): {(r.stderr or r.stdout).strip()}"
        )


def git_worktree_remove(shared: Path, dest: Path) -> None:
    """Default remover: `git worktree remove <dest>`. Raises on failure."""
    r = subprocess.run(
        ["git", "-C", str(shared), "worktree", "remove", str(dest)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise WorkspaceError(
            f"git worktree remove {dest} failed (exit {r.returncode}): "
            f"{(r.stderr or r.stdout).strip()}"
        )


def git_worktree_holds_work(dest: Path, base: str) -> bool:
    """True if the worktree holds work worth keeping: a dirty tree OR commits not
    already in <base>. Either means removing it would DISCARD work, which is the
    internal-ref failure we exist to prevent — so gc keeps it. Uncertainty (a git
    call fails) is treated as HOLDS WORK: never discard on a maybe.
    """
    dirty = subprocess.run(
        ["git", "-C", str(dest), "status", "--porcelain"],
        capture_output=True, text=True)
    if dirty.returncode != 0 or dirty.stdout.strip():
        return True
    ahead = subprocess.run(
        ["git", "-C", str(dest), "rev-list", "--count", f"{base}..HEAD"],
        capture_output=True, text=True)
    if ahead.returncode != 0:
        return True                      # cannot tell -> keep, never discard
    return ahead.stdout.strip() not in ("", "0")


def _shared_repo(repo: Path | str) -> Path:
    """Normalize to the SHARED checkout, tolerating being handed the worktree dir
    or the `<name>-wt` container (crew-worktree.sh does the same on the name)."""
    p = Path(repo).expanduser()
    if p.parent.name.endswith("-wt"):            # <name>-wt/<agent>
        return p.parent.parent / p.parent.name[:-len("-wt")]
    if p.name.endswith("-wt"):                    # <name>-wt
        return p.parent / p.name[:-len("-wt")]
    return p


def worktree_for(repo: Path | str, agent: str) -> Path:
    """Where agent's isolated worktree of <repo> lives: <repo>-wt/<agent>."""
    shared = _shared_repo(repo)
    return shared.parent / f"{shared.name}-wt" / agent


def ensure_worktree(repo: Path | str, agent: str, base: str = "origin/main",
                    add: WorktreeAdd = git_worktree_add) -> str:
    """Guarantee an isolated per-agent worktree off a SHARED project repo; return
    it as a cwd. Idempotent: an existing worktree is returned untouched (present
    means present — never re-fetch/reset a tree that may hold uncommitted work,
    the same rule ensure_workspace keeps).

    RAISE rather than hand back a path we did not verify, exactly like
    ensure_workspace — an agent launched into a half-made worktree is the silent
    failure this refuses.
    """
    shared = _shared_repo(repo)
    if not (shared / ".git").exists():
        raise WorkspaceError(
            f"no shared git checkout at {shared} to provision a worktree from — "
            f"refusing to invent one. Fix: pass the path to the real checkout."
        )
    dest = shared.parent / f"{shared.name}-wt" / agent
    if dest.is_dir():
        return str(dest)                 # already provisioned — idempotent
    if dest.exists():
        raise WorkspaceError(
            f"worktree path for {agent} off {shared} is not a directory: {dest}. "
            f"Refusing to launch into it."
        )
    add(shared, dest, agent, base)
    if not dest.is_dir():
        raise WorkspaceError(
            f"worktree for {agent} off {shared} still does not exist after add: "
            f"{dest}"
        )
    return str(dest)


def cleanup_worktree(repo: Path | str, agent: str, base: str = "origin/main",
                     remove: WorktreeRemove = git_worktree_remove,
                     holds_work: WorktreeHoldsWork = git_worktree_holds_work
                     ) -> bool:
    """Remove agent's worktree IFF it is unchanged; return whether it was removed.

    The isolation:worktree auto-clean — a worktree with no work in it is orphan
    clutter, but a worktree with uncommitted or unpushed work is an agent's
    output, and discarding that is the exact data-loss (internal-ref) this whole
    line of work removes. So: keep on ANY sign of work, and on any uncertainty.
    Absent worktree -> nothing to do, returns False (not an error: gc is
    idempotent).
    """
    dest = worktree_for(repo, agent)
    if not dest.is_dir():
        return False                     # nothing to clean — idempotent
    if holds_work(dest, base):
        return False                     # holds work — keep it, never discard
    remove(_shared_repo(repo), dest)
    return True
