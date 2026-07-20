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


def ensure_workspace(card: Agent, clone: Cloner = git_clone) -> str | None:
    """Guarantee card.workspace exists and return the path Runtime uses as cwd.

    Returns None when the card elects no workspace — that is not a failure, it is
    "launch in the default cwd", which is what compose() already does when
    card.workspace is None. Nothing to ensure, nothing to refuse.
    """
    if not card.workspace:
        return None                      # no workspace elected — nothing to ensure

    path = Path(card.workspace).expanduser()

    if path.is_dir():
        return str(path)                 # IDEMPOTENT: present -> leave it alone

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
