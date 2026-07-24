"""deployment — where a DEPLOYMENT declares its own defaults, and the ONE reader.

    <root>/env.json  (gitignored deployment config)   then   the ambient env

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

import json
import os
from pathlib import Path


def deployment_default(root, key: str) -> str | None:
    """The deployment's declared value for `key`, or None if it never said.

    `root` is the .shanty root (None is allowed — an unrooted caller simply
    skips the file half and reads the env). A malformed or unreadable env.json
    is TREATED AS SILENT, not as an error: deployment config is optional, and a
    hook that died because a file had a stray comma would take the fleet with it.
    """
    if root is not None:
        try:
            loaded = json.loads((Path(root) / "env.json").read_text())
            if isinstance(loaded, dict) and loaded.get(key):
                return str(loaded[key])
        except (OSError, ValueError):
            pass
    return os.environ.get(key) or None
