"""Inspect the environment ssh handed st, without opening a tracker or tmux server."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import sys

from . import config

BACKENDS = {"files", "beads", "br", "forgejo"}


def check(root: Path, *, backend: str | None = None) -> tuple[int, str]:
    """Return a diagnostic and a pasteable recipe; never modify shell startup files.

    An explicit --root lets the operator name the intended deployment while the
    check still catches its absence from the incoming environment. It must run
    before command_environment: a TOML value is not an exported SSH variable.
    """
    root = Path(root).resolve()
    cfg, error = config.load_or_default(root)
    rows = ["Relay environment (this process; run over non-interactive SSH to prove that path):"]
    faults = 0

    def row(ok: bool, name: str, detail: str) -> None:
        nonlocal faults
        faults += not ok
        rows.append(f"  {'OK' if ok else 'MISSING/WRONG'} {name}: {detail}")

    # Never execute binaries found on PATH: this is a setup check, not a send.
    incoming_path = os.environ.get("PATH", "")
    programs = {name: shutil.which(name, path=incoming_path) for name in ("st", "tmux")}
    for name, path in programs.items():
        row(bool(path), f"PATH/{name}", path or f"{name} is not executable on incoming PATH")

    incoming_root = os.environ.get("SHANTY_ROOT", "")
    root_ok = bool(incoming_root) and Path(incoming_root).is_absolute()
    root_ok = root_ok and Path(incoming_root).resolve() == root and root.is_dir()
    row(root_ok, "SHANTY_ROOT", f"expected existing absolute directory {root}; "
        "missing/wrong exports can leave sends UNJOURNALED")

    incoming_backend = os.environ.get("SHANTY_BACKEND", "")
    declared_backend = cfg.env.get("SHANTY_BACKEND")
    expected_backend = backend or declared_backend or incoming_backend or "files"
    backend_ok = (incoming_backend in BACKENDS and incoming_backend == expected_backend
                  and (not declared_backend or declared_backend == expected_backend))
    row(backend_ok, "SHANTY_BACKEND", f"expected {expected_backend!r}; "
        "an unset backend can route a durable send to an unintended beads store")
    if declared_backend and backend and declared_backend != backend:
        rows.append("  The requested backend conflicts with [env] SHANTY_BACKEND; reconcile the deployment config.")
    if not backend and not declared_backend and not incoming_backend:
        rows.append("  files is the suggested standalone backend; select your intended backend with --backend.")
    if error:
        rows.append(f"  UNKNOWN deployment config: {error}")

    # Include only known executable locations, not a copy of the caller's PATH
    # (which can contain transient virtualenvs). Quote literal paths separately
    # from the intentionally live $PATH expansion.
    directories = [str(Path.home() / ".local" / "bin")]
    if sys.platform == "darwin":
        directories += ["/opt/homebrew/bin", "/usr/local/bin"]
    directories += [str(Path(p).parent) for p in programs.values() if p]
    directories = list(dict.fromkeys(directories))
    rows += ["", "For zsh, put these exports together in ${ZDOTDIR:-$HOME}/.zshenv:",
             "  export PATH=" + shlex.quote(":".join(directories)) + ':"$PATH"',
             "  export SHANTY_ROOT=" + shlex.quote(str(root))]
    if expected_backend in BACKENDS:
        rows.append("  export SHANTY_BACKEND=" + shlex.quote(expected_backend))
    else:
        rows.append("  Choose --backend files|beads|br|forgejo before setting SHANTY_BACKEND.")
    rows += ["", "Install missing st/tmux first. For other shells, use their non-interactive startup mechanism.",
             "Verify from the sending host (replace user@peer):",
             "  ssh -o BatchMode=yes user@peer " + shlex.quote(
                 f"st --root {shlex.quote(str(root))} ops doctor --relay"),
             "No files changed; no message sent. This checks environment, not delivery or tracker connectivity."]
    return (2 if error else 1 if faults else 0), "\n".join(rows)
