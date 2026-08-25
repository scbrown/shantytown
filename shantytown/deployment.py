"""deployment — where a DEPLOYMENT declares its own defaults, and the ONE reader.

    <root>/shantytown.toml [env]   then   the ambient env

That order is the one the launch side already used for carried env,
SHANTY_BASH_GUARD and SHANTY_STOP_CAPTURE, and the one the CLI already used for
SHANTY_BACKEND / SHANTY_BEADS_REPO. It is not arbitrary: a public repo must not
embed a tracker path or an internal hostname, so the deployment says "my tracker
is beads at <repo>" ONCE, in a file that is not checked in, and every surface
reads it from there.

WHY THIS FILE EXISTS. Those same six lines were written THREE times — cli.
_deployment_default, runtime.bash_guard_command, runtime.stop_capture_command —
and a fourth caller (untracked.py, which must see the deployment's real tracker
backend or it warns every agent on a store it never looked at) would have made
four. Three copies of a resolver is how "where deployment config lives" quietly
acquires two answers; this repo has already paid for that once (aegis-tisp: a
fleet whose plates live in beads rendered a BLANK plate, consistently and with
exit 0, because one surface guessed `files` while the deployment said otherwise).
One reader, so the copies cannot drift.

Absent from both sources -> None, and every caller treats None as "the
deployment did not say", never as a value.
"""
from __future__ import annotations

import os
from pathlib import Path


# --- WHERE the store is -----------------------------------------------------
#
# st is POINTABLE, not a home. An agent should not have to live under the st
# checkout, carry st's environment, or have been launched by st for `st anchor`
# to work — pointing a small harness at panes that already exist is the whole
# pitch. Discovery is what makes that true; ownership is separate and unchanged
# (st still only STOPS or RESPAWNS what it launched).
#
# THE CHAIN, most explicit first:
#
#   --root          the caller said so. Nothing may override it.
#   $SHANTY_ROOT    the environment said so — what the launcher bakes into every
#                   agent it starts (harness.py).
#   walk-up         a .shanty in cwd or any ancestor: "anywhere inside the
#                   project" works, the way git and .beads already behave.
#   pointer         ~/.config/shantytown/root — the deployment this box uses.
#   cwd/.shanty     the last resort, and usually the thing that does not exist.
#
# THE POINTER IS NOT REDUNDANT WITH THE WALK-UP, and the case that forced it is
# measured (aegis-d94vb): the store is ~/gt/shantytown/.shanty and a crew
# workspace is ~/gt/beads_aegis/crew/<agent>. That is a SIBLING tree, not an
# ancestor, so no amount of walking up reaches it — and `st anchor <me>`, the
# first line of every crew member's instructions, refused from the one directory
# they were told to run it in. A walk-up alone would have left that broken.
#
# EVERY ANSWER CARRIES HOW IT WAS REACHED. "st read the wrong store" and "st read
# an empty store" are indistinguishable otherwise, and this repo has already paid
# for that once (a blank plate at exit 0).

BY_FLAG = "--root"
BY_ENV = "$SHANTY_ROOT"
BY_WALKUP = "walk-up"
BY_POINTER = "pointer"
BY_CWD = "cwd"

_POINTER_REL = Path("shantytown") / "root"


def pointer_path() -> Path:
    """~/.config/shantytown/root, honouring $XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / _POINTER_REL


def read_pointer() -> Path | None:
    """The deployment this box points at, or None.

    A pointer naming a directory that is NOT there returns None rather than a
    path: a store that does not exist is not an answer, and returning one would
    turn a stale pointer into "your crew is empty" instead of "keep looking".
    """
    try:
        text = pointer_path().read_text().strip()
    except OSError:
        return None
    if not text:
        return None
    p = Path(text).expanduser()
    return p if p.is_dir() else None


def write_pointer(root) -> Path:
    """Point this box at `root`. Written by `st init`; the file is one line so it
    can be read and corrected with `cat` and an editor."""
    path = pointer_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{Path(root).resolve()}\n")
    return path


def resolve_root(explicit=None, *, cwd=None, discover: bool = True) -> tuple[Path, str]:
    """(root, how) — THE resolver. See the chain above.

    `discover=False` stops after the environment and answers cwd/.shanty. That is
    for `st init`, which must never adopt a store it merely FOUND: init in a new
    project directory, silently resolving to the deployment the pointer names,
    would refuse as "already a deployment" while pointing at somebody else's.
    Creating is not the same act as finding.
    """
    if explicit is not None:
        return Path(explicit), BY_FLAG
    env = os.environ.get("SHANTY_ROOT")
    if env:
        return Path(env), BY_ENV
    here = Path(cwd) if cwd else Path.cwd()
    if not discover:
        return here / ".shanty", BY_CWD
    for d in (here, *here.parents):
        candidate = d / ".shanty"
        if candidate.is_dir():
            return candidate, BY_WALKUP
    pointed = read_pointer()
    if pointed is not None:
        return pointed, BY_POINTER
    return here / ".shanty", BY_CWD


def root_note(how: str) -> str:
    """One clause naming how a root was chosen, for an error or a header."""
    if how == BY_POINTER:
        return f"chosen by {pointer_path()}"
    if how == BY_WALKUP:
        return "found by walking up from the current directory"
    if how == BY_CWD:
        return "the current directory's .shanty (nothing else answered)"
    return f"given by {how}"


def deployment_default(root, key: str) -> str | None:
    """The deployment's declared value for `key`, or None if it never said.

    PRECEDENCE: `[env]` in shantytown.toml, then the ambient environment.

    `root` is the .shanty root (None is allowed — an unrooted caller simply
    skips the file half and reads the env). A malformed or unreadable file of
    either kind is TREATED AS SILENT, not as an error: deployment config is
    optional, and a hook that died because a file had a stray comma would take the
    fleet with it. (`load_or_default`, not `load`, for exactly that reason.)

    THE FILE STILL BEATS THE ENV. A deployment declaration must not change
    meaning according to whatever a stray shell export happens to hold.
    """
    if root is not None:
        from .config import load_or_default
        cfg, _err = load_or_default(root)
        if cfg.env.get(key):
            return cfg.env[key]
    return os.environ.get(key) or None
