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
from dataclasses import dataclass
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


def unlaunchable(card: Agent) -> str | None:
    """Why an UNATTENDED respawn of this card would not land in its own tree —
    or None if it would. PURE: no clone, no mkdir, no network, no git.

    THE ASYMMETRY THIS EXISTS FOR (aegis-6hfmi). `ensure_workspace` below is
    right that `workspace=None` is not a failure: it means "launch in the
    default cwd", and for a HAND launch that is a real election — the operator
    is standing in the directory they meant. But a card armed for AUTOMATIC
    respawn is launched by a supervisor, and the supervisor's cwd is whatever
    systemd handed it. Nobody elected that. So the same value is a considered
    choice on one path and an unnoticed gap on the other, and only the caller
    knows which path it is on. That is why this is a SEPARATE, opt-in check and
    not a new refusal inside ensure_workspace: making it unconditional would
    break the legitimate hand launch, and leaving it out is what let ian be
    re-armed for respawn into a directory it does not live in.

    Consequently this is a question about ARMING, not about launching. It is
    the check `st tend --unretire` runs, because un-retiring is the moment a
    card goes from "the supervisor ignores it" to "the supervisor will start
    it", and that transition had no pre-flight at all: `--unretire` re-armed
    whatever it was pointed at and nothing warned.

    ABSENT-BUT-CLONABLE IS NOT A FAULT. A card with a workspace_source and no
    directory yet is exactly the case ensure_workspace was built to handle; it
    clones at respawn. Reporting that as unlaunchable would refuse the working
    path and teach operators to reach for --force, which is how a gate stops
    being read.

    WHAT IT DELIBERATELY DOES NOT CHECK: whether an existing tree is a clone of
    the RIGHT remote. That answer needs git, and this runs in front of an
    interactive command that must stay instant and offline. ensure_workspace
    still makes that call at launch, where it is a refusal that creates
    nothing. So None here means "no fault visible without touching the disk",
    never "verified launchable" — the narrower claim is the honest one.
    """
    if not card.workspace:
        return (f"{card.name} carries NO workspace, so a respawn would launch "
                f"it in whatever directory the supervisor happens to run in — "
                f"not in its own tree, and not with its own CLAUDE.md or "
                f".mcp.json. An agent launched there comes up, looks alive in "
                f"`st crew`, and reads someone else's charter or none at all. "
                f"Fix: `st role set {card.name} --workspace <dir>`")

    path = Path(card.workspace).expanduser()
    if path.is_dir():
        return None                      # present — the launch has a tree to use
    if path.exists():
        return (f"{card.name}'s workspace is not a directory: {path}. A "
                f"respawn would refuse at launch. Fix: point workspace at a "
                f"directory, or move {path} aside.")
    if not card.workspace_source:
        return (f"{card.name}'s workspace does not exist: {path}, and the card "
                f"carries no workspace_source to clone it from — so a respawn "
                f"would refuse at launch, every time, forever. Fix: create the "
                f"directory, or set workspace_source on the card.")
    return None                          # absent but clonable — the designed path


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
        # PRESENT IS NOT THE SAME AS CORRECT (aegis-8p0j gap 2). This used to be a
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
# Worktrees for SHARED PROJECT repos (aegis-h2rr).
#
# ensure_workspace above owns the agent's OWN clone (crew/<name>): one writer,
# one tree. This owns the other case: a SHARED project repo — ~/gt/shantytown,
# ~/gt/quipu, ~/gt/hank, ~/gt/goldblum — that many agents touch at once. A shared
# checkout shares its index and HEAD with every process in it, so one agent's
# `git add`/`commit`/`reset` reaches into another's staging area and BOTH SIDES
# report success (measured: aegis-repg/iaef — a commit left `git log` entirely and
# nothing errored). The fix is not a guard that refuses the commit; it is to give
# each agent its OWN worktree off the shared repo, so its index/HEAD are its own
# and the shared checkout is never the write surface for two agents.
#
# This used to be a MANUAL step: agents were told to run scripts/crew-worktree.sh
# / `git worktree add` by hand, and a reminder was put in crew CLAUDE.md. A manual
# discipline is the vigilance-not-mechanism failure this fleet keeps paying for
# (aegis-h2rr). st provisions the worktree now; the agent never has to remember.
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
    aegis-repg failure we exist to prevent — so gc keeps it. Uncertainty (a git
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


# Running git is INJECTED here for the same reason cloning and origin-reading are
# above: the refusal has to be testable without a network. Returns (rc, stdout).
GitRunner = Callable[..., "tuple[int, str]"]

MAIN_CANDIDATES = ("main", "master")


def _git(dest: Path | str, *args: str) -> "tuple[int, str]":
    r = subprocess.run(["git", "-C", str(dest), *args],
                       capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout or "").strip()


def upstream_ref(dest: Path | str, run: GitRunner = _git
                 ) -> "tuple[str | None, str | None]":
    """WHICH REF DOES "current" MEAN FOR THIS REPO? Returns (ref, note).

    `ref` is None when we cannot tell, and then `note` is the refusal. Otherwise
    `note` is None, or a WARNING that some OTHER remote is ahead of the ref we
    chose — which the caller must surface, never swallow.

    WHY THIS IS NOT JUST "origin/main" (aegis-ib65p). Every worktree entry point
    here defaulted to the literal string `origin/main`, and on the repo this was
    written for that is the WRONG TREE:

        forge   ssh://.../shantytown.git    <- what `main` actually tracks
        origin  git@github.com:...          <- a public mirror

    Measured twice, three hours apart, and the answer INVERTED between them: the
    mirror was 1 commit BEHIND, then 1 commit AHEAD. Both remotes take real
    pushes, so there is no static answer, and a guard that rebased every worktree
    onto `origin/main` would at one of those two moments have moved twelve
    worktrees BACKWARD relative to the authority while printing "current". That
    is the staleness this is meant to remove, reintroduced by the remedy and
    dressed as a success line.

    So the ref is RESOLVED FROM CONFIGURATION, never from a remote's name:

      1. `<main>@{upstream}` — what the repo itself says its main branch tracks.
         The only defensible automatic answer, and per-repo correct.
      2. Exactly one remote and no upstream config -> use it. Unambiguous.
      3. Two or more remotes and nothing configured -> REFUSE, naming them.

    Step 3 is the same doctrine `normalize_source` already states for clone
    sources — "a guessed remote is how an agent gets launched into the wrong
    repo" — and the cost asymmetry is identical. A false refusal is loud, names
    both remotes, and a human fixes it in one `git branch --set-upstream-to`. A
    wrong ref is undetectable: the tree looks current, the agent works, and it
    rebuilds something that already exists. Not a close call.

    THE `note` IS THE POINT OF THE WHOLE FUNCTION, not a decoration. The bug this
    serves is an agent not knowing what it does not have. Picking one remote and
    staying SILENT about a second one that is ahead is that same bug with a
    tidier surface, so a divergence is always reported even though it is never
    acted on automatically.
    """
    rc, remotes_out = run(dest, "remote")
    remotes = [r for r in remotes_out.splitlines() if r.strip()] if rc == 0 else []

    ref = None
    branch = None
    for b in MAIN_CANDIDATES:
        rc, out = run(dest, "rev-parse", "--abbrev-ref", f"{b}@{{upstream}}")
        if rc == 0 and out:
            ref, branch = out, b
            break

    if ref is None:
        if len(remotes) == 1:
            for b in MAIN_CANDIDATES:
                cand = f"{remotes[0]}/{b}"
                rc, _ = run(dest, "rev-parse", "--verify", "--quiet", cand)
                if rc == 0:
                    ref, branch = cand, b
                    break
        if ref is None:
            if len(remotes) > 1:
                return None, (
                    f"cannot tell which remote is authoritative for {dest}: "
                    f"{', '.join(sorted(remotes))} are all configured and no "
                    f"main branch tracks one of them. REFUSING to guess — the "
                    f"wrong remote silently rebases onto the wrong tree. Fix: "
                    f"`git -C {dest} branch --set-upstream-to=<remote>/main main`.")
            return None, (
                f"cannot resolve an upstream ref for {dest} "
                f"({'no remotes configured' if not remotes else 'no main/master branch found'}).")

    # A DIVERGENCE IS REPORTED, NEVER ACTED ON. We do not switch refs, merge, or
    # pick the "more advanced" remote — choosing between two authorities is a
    # human's call. We only refuse to be quiet about it.
    # EVERY main-candidate on EVERY remote, not just the same branch name on a
    # different remote (widened after the shanty TOMBSTONE, aegis-ib65p).
    #
    # The narrow version compared only `<other-remote>/<same-branch>`, which
    # cannot see the case that actually bit this fleet: shanty's worktrees track
    # `forge/master`, and `forge/master` is a TOMBSTONE — one commit reading
    # "shanty has moved to github" — while `forge/main` carries the live work.
    # Same remote, different branch, so the old check was blind to it and
    # `st worktree` would have reported "current with forge/master" while sitting
    # on a dead branch.
    #
    # Config-resolution alone does NOT save you here, and that is the honest
    # limit worth stating: the branch really does track the tombstone, so "ask
    # the repo" faithfully returns the wrong answer. Configuration can be stale
    # in exactly the way a hardcoded name can. What saves you is refusing to be
    # quiet — the ref is still used (we do not silently switch branches under
    # anyone), but a live branch that the chosen ref does not contain is always
    # named, and a human moves the upstream.
    chosen_remote = ref.split("/", 1)[0]
    ahead = []
    seen_refs = {ref}
    for r in remotes:
        for b in MAIN_CANDIDATES:
            other = f"{r}/{b}"
            if other in seen_refs:
                continue
            seen_refs.add(other)
            rc, _ = run(dest, "rev-parse", "--verify", "--quiet", other)
            if rc != 0:
                continue
            rc, out = run(dest, "rev-list", "--count", f"{ref}..{other}")
            if rc == 0 and out.isdigit() and int(out) > 0:
                rc2, sha = run(dest, "rev-parse", "--short", other)
                ahead.append(f"{other} is {out} ahead ({sha if rc2 == 0 else '?'})")
    if ahead:
        return ref, (f"NOTE: {'; '.join(ahead)} — {ref} is what this repo tracks, "
                     f"so that is what was used, but work exists that it does not "
                     f"contain.")
    return ref, None


@dataclass(frozen=True)
class Staleness:
    """How a tree stands against its upstream — IN BOTH DIRECTIONS.

    Both risks were live in this fleet on one evening (aegis-ib65p), and they are
    not the same risk, so collapsing them into one "stale" bit would hide one of
    them:

      behind    commits on the ref that this tree does NOT have.
                Risk: DUPLICATION. The coordinator rebuilt a status-bar fix from
                scratch that already existed, and the branches later auto-merged
                into a duplicate map key that only the compiler caught.
      unpushed  commits here that are on NO REMOTE REF AT ALL.
                Risk: LOSS. Work that exists in exactly one place, invisible
                to everyone, and destroyed by any of the resets this codebase
                already forbids (aegis-repg/iaef).

                QUALIFIED BY WHAT WE HAVE FETCHED, and the wording says so.
                `--remotes` means remote-tracking refs THIS TREE KNOWS, so a
                branch that was pushed but never fetched here reads as stranded.
                Measured: four commits reported at-risk in gastown-wt/harding
                were all on `origin/aegis-ri87-on-installed`, and a later fetch
                dropped the count to zero without a single byte moving. Two
                separate false-positive reports went out on this metric before
                it was stated plainly, which is why the string now claims only
                what the data supports.

                MEASURED AGAINST EVERY REMOTE REF, NOT AGAINST THE UPSTREAM
                (corrected by tim, aegis-ib65p). The first version counted
                `<ref>..HEAD` — commits not on origin/main — and that is a
                different and much larger set: it counts every in-flight FEATURE
                BRANCH as stranded. Measured false positive: hank-wt/tim was
                reported "+1, exists only here" while the commit was sitting on
                `origin/hank-1-daemon-stage3c`, pushed and mid-review. Nothing
                was at risk.

                That error runs in the WORST direction for this particular
                warning. A loss alarm earns attention only by being rare; one
                that fires on every open feature branch on the fleet trains
                everyone to dismiss it, and then the one genuinely stranded
                commit is dismissed with the rest. `HEAD --not --remotes` asks
                the question we actually mean — "does this exist anywhere but
                here" — instead of a proxy for it.

    `dirty` is uncommitted work — the reason a refresh must REPORT rather than
    act. `note` carries a remote divergence or a refusal from upstream_ref.
    """
    ref: str | None = None
    behind: int = 0
    unpushed: int = 0
    dirty: bool = False
    note: str | None = None
    error: str | None = None

    def current(self) -> bool:
        """Nothing to fetch and nothing stranded. `dirty` is deliberately NOT
        part of this: uncommitted work is normal mid-task and is not staleness."""
        return self.error is None and self.behind == 0 and self.unpushed == 0

    def render(self) -> str:
        """One line, and it must never flatter. Silence about a thing we could
        not measure is how the fleet learned to trust a stale tree."""
        if self.error:
            return f"staleness UNKNOWN ({self.error})"
        bits = []
        if self.behind:
            bits.append(f"{self.behind} behind {self.ref} (work you do NOT have "
                        f"— check for duplication before you build)")
        if self.unpushed:
            bits.append(f"{self.unpushed} on no remote ref KNOWN LOCALLY "
                        f"(as of the last fetch — fetch before treating as lost)")
        if self.dirty:
            bits.append("uncommitted changes")
        if not bits:
            return f"current with {self.ref}"
        return "; ".join(bits)


def tree_staleness(dest: Path | str, run: GitRunner = _git,
                   fetch: bool = False) -> Staleness:
    """Measure a tree against its resolved upstream. NEVER writes, never pulls.

    `fetch` is OFF by default and that is a design constraint, not laziness: this
    is called from an EDIT-TIME hook (aegis-ib65p decision 5), and a hook that
    hit the network on every edit would be switched off within a day — at which
    point it protects nothing. Reading already-fetched refs is nearly free and is
    honest about what it is: "as of the last fetch". The dispatch path, which
    runs once and can afford it, passes fetch=True.
    """
    ref, note = upstream_ref(dest, run=run)
    if ref is None:
        return Staleness(ref=None, note=note, error=note)
    if fetch:
        # --prune IS LOAD-BEARING, not tidiness (tim, aegis-ib65p). A plain fetch
        # does NOT delete remote-tracking refs for branches deleted upstream, and
        # fetch.prune is unset here at local, global AND system scope. Those dead
        # refs are still `--remotes`, so a commit whose only remote ref has been
        # deleted counts as SAFE - reported as on-a-remote when it is on no
        # remote at all.
        #
        # THE ASYMMETRY IS WHY THIS MATTERS MORE THAN THE LAST TWO CORRECTIONS.
        # Stale BEHIND data over-reports: noise, and noise is survivable. Stale
        # AHEAD data UNDER-reports: a commit declared safe that is already gone,
        # which is the single failure mode a data-loss metric may not have.
        # Measured in a live worktree: 3 refs would prune, each able to launder
        # an orphan into "safe".
        #
        # "As of the last fetch" does NOT cover it, which is the subtle part -
        # the lying ref SURVIVES the fetch. Only the prune removes it.
        run(dest, "fetch", "--all", "--prune", "--quiet")
        ref, note = upstream_ref(dest, run=run)
        if ref is None:
            return Staleness(ref=None, note=note, error=note)
    rc, behind = run(dest, "rev-list", "--count", f"HEAD..{ref}")
    # NOT `{ref}..HEAD` — see Staleness.unpushed. That measures ahead-of-main and
    # flags every open feature branch as stranded; this asks whether the commits
    # exist on ANY remote ref, which is the question "is this work anywhere but
    # here" actually means.
    rc2, unpushed = run(dest, "rev-list", "--count", "HEAD", "--not", "--remotes")
    if rc != 0 or rc2 != 0:
        return Staleness(ref=ref, note=note,
                         error=f"could not count commits against {ref}")
    rc3, porcelain = run(dest, "status", "--porcelain")
    return Staleness(
        ref=ref,
        behind=int(behind) if behind.isdigit() else 0,
        unpushed=int(unpushed) if unpushed.isdigit() else 0,
        # CANNOT TELL IS NOT CLEAN. A failed status must not read as a tidy tree,
        # because the whole point of the flag is to stop a refresh from acting.
        dirty=bool(porcelain.strip()) if rc3 == 0 else True,
        note=note,
    )


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
    output, and discarding that is the exact data-loss (aegis-repg) this whole
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


# ── PUSH TO EVERY REMOTE (aegis-96few, ruled by arnold 2026-08-04) ────────────

@dataclass(frozen=True)
class PushOutcome:
    """What one remote did with the push. `remote` is ALWAYS populated.

    NAMING THE REMOTE IS THE POINT, not a nicety (arnold's requirement 2). A bare
    "push rejected" cannot be acted on when a repo has two live peers: the author
    cannot tell "my branch is behind" from "the other remote moved", and those
    have different next steps. Every field below exists so the failure line can
    say WHICH remote refused and WHY.
    """
    remote: str
    ok: bool
    up_to_date: bool = False
    non_ff: bool = False
    reason: str | None = None


def push_every_remote(dest: Path | str, src: str, dst: str = "main",
                      run: GitRunner = _git) -> "list[PushOutcome]":
    """Push `src` to `dst` on EVERY configured remote. NEVER forces.

    WHY EVERY REMOTE. shantytown has two live peers, neither a mirror, and each
    agent's `wt/<name>` branch is configured to push to one of them — measured
    2026-08-04: 11 agents to forge, 5 to origin. So the one documented recipe
    lands in different places depending on WHOSE tree runs it, and any two agents
    on opposite sides re-fork the repo the moment both push. Every agent is
    correct from inside their own tree, which is why no amount of telling people
    to be careful fixes it: there is no wrong actor. Pushing every remote makes
    the split HARMLESS instead of requiring anyone to resolve it.

    WHY IT REFUSES INSTEAD OF FORCING. A non-fast-forward here means the other
    remote moved — someone else's work is on it. Converging two forked remotes
    never needs a force (a merge commit has both parents, so both pushes are
    fast-forwards; verified twice on 2026-08-04), so a force in this path could
    only ever be destroying work that git and its author both believe landed.
    That is aegis-repg/iaef at remote scale.

    PARTIAL SUCCESS IS REPORTED, NOT UNWOUND. If origin accepts and forge
    refuses, the origin push has happened and is not rolled back — un-pushing is
    a rewrite, which is the very thing this refuses to do. The caller is told
    exactly which remotes took it and which did not, and the fix is the same
    either way: fetch the refusing remote, merge, push again.
    """
    rc, out = run(dest, "remote")
    remotes = sorted(r.strip() for r in out.splitlines() if r.strip()) if rc == 0 else []
    if not remotes:
        return []

    outcomes: list[PushOutcome] = []
    for remote in remotes:
        rc, out = run(dest, "push", "--porcelain", remote, f"{src}:refs/heads/{dst}")
        # --porcelain puts the per-ref result on STDOUT (plain push writes it to
        # stderr, which this runner drops) — that is why it is used here rather
        # than for readability.
        text = out or ""
        if rc == 0:
            outcomes.append(PushOutcome(
                remote=remote, ok=True,
                up_to_date=any(ln.startswith("=") for ln in text.splitlines())))
            continue
        low = text.lower()
        non_ff = ("non-fast-forward" in low or "fetch first" in low
                  or "[rejected]" in low)
        if non_ff:
            reason = (f"{remote} REFUSED: non-fast-forward. {remote} has commits "
                      f"this branch does not. Fetch it and merge — never force: "
                      f"`git fetch {remote} && git merge {remote}/{dst}` then push "
                      f"again. A merge commit fast-forwards on BOTH remotes.")
        else:
            first = next((ln for ln in text.splitlines() if ln.strip()), "")
            reason = f"{remote} FAILED: {first or 'no output from git push'}"
        outcomes.append(PushOutcome(remote=remote, ok=False, non_ff=non_ff,
                                    reason=reason))
    return outcomes
