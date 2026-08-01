"""guard — is the SHARED CHECKOUT of each project repo actually protected?

st already ASSISTS the worktree protocol: `st worktree <repo>` provisions an
agent's isolated worktree, and `st go --worktree` delivers the path in the
dispatch. This module is the other half — ENFORCEMENT — and it exists because
the assisted half was real while the enforced half was a script somebody had to
remember to run.

WHAT THE GUARD IS. A pre-commit/pre-rebase/pre-merge-commit hook that refuses in
the MAIN working copy and passes silently in a linked worktree. Hooks live in the
COMMON git dir, so one install covers the shared checkout and every worktree
hanging off it.

WHY IT IS NEEDED, measured: a shared checkout's index and HEAD belong to the
WORKING COPY, not to a process. Two crew sessions in one checkout can commit each
other's staged files, and one session's `git reset` can drop the other's commit
out of `git log` — with git reporting SUCCESS to both. Neither agent has anything
to notice.

THREE VERDICTS, AND THE THIRD IS THE WHOLE POINT.

    OK        a guard is installed where git will actually read it
    MISSING   no guard
    INERT     a guard is installed and CANNOT RUN

INERT is `core.hooksPath`. When it is set, git ignores the common git dir's
`hooks/` entirely — the exact directory an installer writes into. The install
succeeds, a naive check says "installed", and the hook never fires. That is a
control reporting itself healthy while being structurally unable to act, which is
strictly worse than no control: it is believed. It is not hypothetical here — a
repo in this fleet sets `core.hooksPath` for its own secret-scanning hook, so a
guard installed there today would go into a directory git does not read.

So INERT is its own verdict and never folds into either neighbour. Rounding it to
MISSING would understate it (somebody "fixed" it already, and the fix did
nothing); rounding it to OK is the lie itself.

THE FIX FOR INERT IS TO CHAIN, NEVER TO RELOCATE. Writing the guard into the
hooksPath directory instead would clobber whatever hook already lives there —
which is the entire reason hooksPath was set — trading one silent breakage for
another. So this module DETECTS the effective hooks directory, reports the
conflict, and prints the chain recipe. It will not install into a directory it
does not own.

AND `OK` DOES NOT MEAN SAFE. The guard fires at COMMIT. `git reset` has no hook
and cannot be guarded at all, and two agents can still stomp each other's working
tree long before any hook runs. So every string this module emits says "guard
installed", never "protected" — a seatbelt, not a cage. A verdict that implied
safety would license the behaviour it exists to discourage.

WHICH REPOS ARE "SHARED"? st cannot know in general — a bare clone with one user
is not shared and should not pay for a worktree. What st DOES know is which repos
it has already provisioned a worktree FROM, because it created the `<repo>-wt`
container itself. That is the honest signal and it is what `discover()` uses: the
moment an agent is given a worktree is the moment the shared checkout is in play.

A HARDCODED REPO LIST IS THE FAILURE MODE, not the fix. Measured 2026-08-01: the
deployment's own installer defaulted to a ONE-repo list, an audit by hand found
six, and discovery from the worktree containers found TWELVE — five of which
nobody had ever looked at. Every repo anyone clones next starts unprotected and
nothing says so. That is why this is a doctor check and not a longer constant.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# The history-mutating hooks git will let us refuse. `git reset` is NOT here and
# cannot be: it has no hook. Stated in the guard's own output so nobody reads the
# absence as an oversight.
HOOKS = ("pre-commit", "pre-rebase", "pre-merge-commit")

# The string every shared-checkout guard carries, whoever installed it. It is how
# this module recognises a guard it did not write and LEAVES IT ALONE — a
# deployment that ships its own installer and st must not overwrite each other's
# hook on alternate runs. Interop by a shared marker, not by ownership.
MARKER = "shared-checkout-guard"

# The escape hatch for deliberate maintenance of the shared checkout. The NAME is
# a parameter rather than a constant because a deployment that already documents
# its own variable would otherwise have two, and an agent following the docs would
# be blocked by the guard that is supposed to be helping it.
DEFAULT_OVERRIDE_ENV = "SHANTY_SHARED_CHECKOUT_OK"

OK = "ok"                    # a guard is installed where git reads hooks
MISSING = "missing"          # no guard
INERT = "inert"              # installed, and core.hooksPath means it cannot run
NO_CHECKOUT = "no-checkout"  # a <repo>-wt container with no shared repo behind it
CANNOT_TELL = "cannot-tell"  # git would not answer — never read as either verdict

_EXIT = {OK: 0, MISSING: 1, INERT: 1, NO_CHECKOUT: 1, CANNOT_TELL: 2}


@dataclass(frozen=True)
class Coverage:
    """One shared repo's verdict. `why` is always populated for anything that is
    not OK, because a coverage report you cannot act on is a coverage report."""
    repo: str
    path: Path
    state: str
    why: str = ""
    hooks_path: str | None = None      # the effective dir, when it is not ours


def _git(repo: Path, *args: str) -> str | None:
    """A git read. None = git would not answer, which is CANNOT_TELL upstream and
    never silently one of the real verdicts."""
    try:
        r = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def common_git_dir(repo: Path) -> Path | None:
    raw = _git(repo, "rev-parse", "--git-common-dir")
    if raw is None:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = repo / p
    try:
        return p.resolve()
    except OSError:
        return None


def effective_hooks_dir(repo: Path, gitdir: Path) -> tuple[Path, bool]:
    """(where git ACTUALLY reads hooks, is_ours).

    `is_ours` is False exactly when core.hooksPath redirects git away from the
    common git dir's hooks/ — the INERT condition. Resolved rather than compared
    as strings: a relative hooksPath, a symlink, or a path that happens to point
    back at our own hooks dir must all give the right answer.
    """
    ours = gitdir / "hooks"
    raw = _git(repo, "config", "--get", "core.hooksPath")
    if not raw:
        return ours, True
    p = Path(raw)
    if not p.is_absolute():
        p = repo / p
    try:
        p = p.resolve()
    except OSError:
        return p, False
    return p, p == ours


def guard_in(hooks_dir: Path) -> bool:
    """Is a shared-checkout guard installed in this directory?

    ANY of the three hooks counts. Deliberately not all-three: a repo that guards
    commit but not rebase is imperfectly covered, not UNcovered, and reporting it
    as MISSING would send someone to install what is already there instead of to
    the real gap. The install path is what makes the set complete.
    """
    for h in HOOKS:
        p = hooks_dir / h
        try:
            if p.is_file() and MARKER in p.read_text(errors="ignore"):
                return True
        except OSError:
            continue
    return False


def inspect(repo: Path | str) -> Coverage:
    """One repo's coverage verdict. Never raises — a verdict is the product."""
    repo = Path(repo).expanduser()
    name = repo.name
    if not (repo / ".git").exists():
        return Coverage(name, repo, NO_CHECKOUT,
                        why=f"no shared checkout at {repo} — a worktree container "
                            f"exists for it, so something provisioned from a repo "
                            f"that is not there now")
    gitdir = common_git_dir(repo)
    if gitdir is None:
        return Coverage(name, repo, CANNOT_TELL,
                        why="git would not report --git-common-dir here")
    hooks_dir, ours = effective_hooks_dir(repo, gitdir)
    if not ours:
        # core.hooksPath redirects git. The guard is effective ONLY if the
        # redirect target chains it — which is the sanctioned fix, and the reason
        # this is checked rather than assumed. Checking makes "chained" provable
        # by the same command that reported the problem.
        if guard_in(hooks_dir):
            return Coverage(name, repo, OK, hooks_path=str(hooks_dir),
                            why=f"guard installed, CHAINED from core.hooksPath "
                                f"({hooks_dir})")
        installed_but_dead = guard_in(gitdir / "hooks")
        lead = ("a guard IS installed in the common git dir and CANNOT RUN"
                if installed_but_dead else "no guard git will read")
        return Coverage(
            name, repo, INERT, hooks_path=str(hooks_dir),
            why=(f"core.hooksPath -> {hooks_dir}, so git never reads "
                 f"{gitdir / 'hooks'}; {lead}. Fix by CHAINING from "
                 f"{hooks_dir}/pre-commit — never by relocating the guard, which "
                 f"would clobber the hook hooksPath was set for"))
    if guard_in(hooks_dir):
        return Coverage(name, repo, OK, why=f"guard installed ({hooks_dir})")
    return Coverage(name, repo, MISSING,
                    why=f"no guard in {hooks_dir} — commits in this shared "
                        f"checkout are unguarded")


def _repo_behind(container: Path) -> Path | None:
    """The shared checkout a `<name>-wt` container's worktrees actually hang off.

    ASK GIT, DO NOT INFER FROM THE NAME. The name is a convention st follows when
    it provisions; it is not a fact, and treating it as one was wrong on two of
    twelve containers the first time this ran against a real fleet:

        hank-wt/*     ->  git says  ~/gt/hank-build/.git     (not ~/gt/hank,
                          which exists and is not even a repo)
        gastown-wt/*  ->  git says  ~/workspace/gastown/.git (a shared repo
                          OUTSIDE the root entirely, so name inference could
                          never have found it at all)

    Both would have been reported as "no shared checkout" — a stale-container
    shrug — while the real repos behind them went unguarded and unnamed. A
    linked worktree records its origin in its own `.git` file, so the answer is
    already on disk; inferring it was the bug.
    """
    try:
        kids = sorted(p for p in container.iterdir() if p.is_dir())
    except OSError:
        return None
    for kid in kids:
        common = _git(kid, "rev-parse", "--git-common-dir")
        if not common:
            continue
        p = Path(common)
        if not p.is_absolute():
            p = kid / p
        try:
            p = p.resolve()
        except OSError:
            continue
        # <repo>/.git -> <repo>. A bare/worktree-only layout has no working copy
        # to guard, and `inspect` will say so rather than guessing.
        return p.parent if p.name == ".git" else p
    return None


def discover(root: Path | str | None = None) -> list[Path]:
    """The shared repos st knows are IN PLAY: one per `<repo>-wt` container it has
    provisioned, resolved through git rather than through the container's name.

    See the module docstring for why this is discovered and not configured — a
    constant is the failure mode this check exists to catch. Deduplicated and
    stably ordered: two containers can legitimately hang off one repo, and the
    same repo must not be reported (or installed into) twice.
    """
    root = Path(root).expanduser() if root else Path(
        os.environ.get("GT_ROOT", Path.home() / "gt"))
    try:
        containers = sorted(p for p in root.iterdir()
                            if p.is_dir() and p.name.endswith("-wt"))
    except OSError:
        return []
    out: list[Path] = []
    seen: set[Path] = set()
    for c in containers:
        repo = _repo_behind(c) or root / c.name[: -len("-wt")]
        if repo not in seen:
            seen.add(repo)
            out.append(repo)
    return out


def survey(root: Path | str | None = None) -> list[Coverage]:
    return [inspect(r) for r in discover(root)]


def worst_exit(rows: list[Coverage]) -> int:
    """2 (could not tell) outranks 1 (a real gap) outranks 0 — the same ordering
    every other checker here uses. An unreadable repo is not a pass."""
    return max((_EXIT.get(r.state, 2) for r in rows), default=0)


_GLYPH = {OK: "✓", MISSING: "✗", INERT: "!", NO_CHECKOUT: "?", CANNOT_TELL: "?"}


def render(rows: list[Coverage]) -> str:
    """The doctor section. Wording is deliberate: `guard installed`, never
    `protected` — the guard fires at commit, and `git reset` has no hook at all.
    A green column that implied safety would license the behaviour it exists to
    discourage."""
    if not rows:
        return ("  shared repos   none in play (no `<repo>-wt` container yet) — "
                "nothing to guard")
    lines = ["  SHARED CHECKOUTS (guard installed ≠ safe: the guard fires at "
             "COMMIT, and `git reset` has no hook)"]
    for r in sorted(rows, key=lambda x: x.repo):
        lines.append(f"    {_GLYPH.get(r.state, '?')} {r.repo:<14} {r.state:<11} "
                     f"{r.why}")
    # THE SUMMARY MUST NOT CONTRADICT THE ROWS. The first version of this counted
    # only MISSING/INERT as bad and therefore printed "12/12 carry the guard"
    # while two rows were NO_CHECKOUT and the exit code was 1 — a summary that
    # reads as a pass over a report that is not one. Denominator is the repos
    # that could carry a guard; anything unresolvable is counted OUT of it and
    # named on its own line, never silently folded into either side.
    guarded = [r for r in rows if r.state == OK]
    bad = [r for r in rows if r.state in (MISSING, INERT)]
    inert = [r for r in rows if r.state == INERT]
    odd = [r for r in rows if r.state in (NO_CHECKOUT, CANNOT_TELL)]
    real = len(guarded) + len(bad)
    lines.append("")
    if bad:
        lines.append(f"    {len(guarded)}/{real} guarded · {len(bad)} unguarded — "
                     f"`st worktree <repo>` installs it")
        if inert:
            lines.append(f"    {len(inert)} INERT: a guard is present and cannot "
                         f"run. This is NOT 'missing' — somebody already 'fixed' "
                         f"it and the fix does nothing.")
    elif real:
        lines.append(f"    {len(guarded)}/{real} carry the guard")
    if odd:
        lines.append(f"    {len(odd)} not counted ({', '.join(r.repo for r in odd)}): "
                     f"a worktree container with no readable repo behind it. NOT a "
                     f"pass — installing is not the fix, so it is excluded from the "
                     f"count rather than inflating it.")
    return "\n".join(lines)


# --- the guard st installs ----------------------------------------------------

def guard_body(override_env: str = DEFAULT_OVERRIDE_ENV) -> str:
    """The hook script, as text.

    POSIX sh, no dependencies, and it carries MARKER so any other installer
    recognises it as a guard rather than as a foreign hook to preserve.

    THE WHOLE TEST is `--absolute-git-dir` vs `--git-common-dir`: they are the
    same path in the main working copy and differ in a linked worktree. That is
    what makes this structural rather than a naming convention — an agent cannot
    be in a worktree and be caught, or be in the shared checkout and slip past.
    """
    return f"""#!/bin/sh
# {MARKER} — installed by `st worktree`. Refuses history-mutating git
# operations in the MAIN working copy of a repo that concurrent sessions share;
# passes silently in a linked worktree, where index and HEAD are per-agent.
#
# Bypass (deliberate maintenance only):  {override_env}=1 git commit ...
# Chain: if <common-git-dir>/hooks/<hook>.local exists it runs after this passes.
#
# `git reset` has NO hook and is NOT guarded. This is a seatbelt, not a cage.
set -u
hook=$(basename "$0")

git_dir=$(git rev-parse --absolute-git-dir 2>/dev/null) || exit 0
common=$(git rev-parse --git-common-dir 2>/dev/null) || exit 0
common=$(cd "$common" 2>/dev/null && pwd -P) || exit 0
git_dir=$(cd "$git_dir" 2>/dev/null && pwd -P) || exit 0

chain() {{
  if [ -x "$common/hooks/$hook.local" ]; then exec "$common/hooks/$hook.local" "$@"; fi
  exit 0
}}

[ "$git_dir" != "$common" ] && chain "$@"        # linked worktree — this is fine
[ "${{{override_env}:-}}" = "1" ] && chain "$@"  # explicit override

root=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
repo=$(basename "$root")
me="${{SHANTY_AGENT:-$(id -un)}}"

cat >&2 <<EOF

BLOCKED: '$hook' in the SHARED checkout $root

  Every concurrent session uses this one working copy, and git's index and HEAD
  belong to the WORKING COPY, not to your process. Another session's commit can
  take your staged files, and its reset can drop your commit out of git log --
  with git reporting success to both of you. Neither of you gets an error.

  Work in your own worktree instead:

      st worktree $repo $me
      cd "\\$(st worktree $repo $me)"
      git fetch origin && git rebase origin/main
      ... work, commit ...
      git push origin wt/$me:main      # rejected? rebase and retry, NEVER force

  Deliberate maintenance of the shared checkout only:
      {override_env}=1 git ...

EOF
exit 1
"""


class GuardError(RuntimeError):
    """Installation could not be done, and the message says what to do instead."""


def install(repo: Path | str, *, override_env: str = DEFAULT_OVERRIDE_ENV
            ) -> tuple[bool, str]:
    """Install the guard into `repo`'s shared checkout. (changed, note).

    IDEMPOTENT AND SILENT WHEN ALREADY DONE: `changed=False, note=""` so a
    re-provision prints nothing. A provisioning command that narrates a no-op
    every time trains people to stop reading it.

    IT NEVER REPLACES A GUARD IT DID NOT WRITE. A deployment shipping its own
    installer and st would otherwise overwrite each other on alternate runs, and
    the two guards' override variables differ — so an agent following the
    deployment's documented bypass would be blocked by st's copy. Recognition is
    by MARKER, which both carry.

    IT REFUSES ON INERT rather than relocating. Writing into the core.hooksPath
    directory would clobber the hook that is the reason hooksPath was set. The
    caller gets the chain recipe.
    """
    repo = Path(repo).expanduser()
    cov = inspect(repo)
    if cov.state == OK:
        return False, ""
    if cov.state == INERT:
        raise GuardError(
            f"{cov.repo}: core.hooksPath sends git to {cov.hooks_path}, so a "
            f"guard installed in the git dir CANNOT RUN. Chain it instead — add "
            f"to {cov.hooks_path}/pre-commit:\n"
            f"        <guard> \"$@\" || exit $?\n"
            f"    Refusing to install a hook that would never fire.")
    if cov.state in (NO_CHECKOUT, CANNOT_TELL):
        raise GuardError(f"{cov.repo}: {cov.why}")

    gitdir = common_git_dir(repo)
    if gitdir is None:
        raise GuardError(f"{cov.repo}: git would not report --git-common-dir")
    hooks_dir = gitdir / "hooks"
    body = guard_body(override_env)
    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
        for h in HOOKS:
            target = hooks_dir / h
            # PRESERVE A FOREIGN HOOK, never delete it: it is somebody's working
            # check, and the guard chains to it after passing. Only once — a
            # second install must not bury the first .local under a new one.
            if target.exists() and MARKER not in target.read_text(errors="ignore"):
                local = target.with_suffix(target.suffix + ".local")
                if not local.exists():
                    target.rename(local)
            target.write_text(body)
            target.chmod(0o755)
    except OSError as e:
        raise GuardError(f"{cov.repo}: could not write hooks into {hooks_dir}: "
                         f"{e}") from e
    return True, (f"guard installed in {repo.name} ({', '.join(HOOKS)}) — commits "
                  f"in the shared checkout are now refused; work in the worktree")
