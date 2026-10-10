"""session_anchor — every session starts holding its work (aegis-7edci3).

`st anchor` answers the three questions a fresh session needs first: who am I,
what is on my plate, who hears my stops. Until this hook, nothing ASKED it. A
session got identity and plate only if the dispatch note typed into its pane
happened to carry them, or if the agent thought to run `st anchor` by itself —
at exactly the moment it had just been emptied of the knowledge that it should.

Measured before this hook (2026-09-30): `st anchor` appeared in the SessionStart
group of 0 of 3 emitted role settings files, on both a Linux fleet and a Mac.

So SessionStart runs the anchor and injects it. Claude Code fires SessionStart
for `startup`, `resume`, `clear` and `compact` (and injects the hook's stdout as
context for each), so one matcher-free registration covers a fresh launch, a
cycle and the far side of a compaction. That is the post-compact re-anchor, with
no second hook to keep in step.

THE SAME RENDER AS `st anchor`, in-process, so the two cannot disagree: this is
`cli.main(["anchor", <agent>])` with its stdout captured. Anything anchor learns
to say later, the session start says too.

FAIL-OPEN, ALWAYS, like resume_brief: every path returns 0, because a hook that
can refuse a session start can take an agent off the fleet. But an anchor that
could not LOOK says so in one line instead of going quiet — silence here would
read as "nothing on your plate", which is the one wrong answer a plate surface
must never give (anchor's own exit 2: "I could not look" is not "fine").
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import sys

# Recognisable in the transcript, to the agent, to a human reading the pane,
# and to a test. One spelling, named once.
BANNER = "[st anchor — session start]"

# anchor's first line: "  You are <name> — <role>[, reports to <lead>]."
_ROLE_RE = re.compile(r"You are \S+ — ([A-Za-z0-9_-]+)")

_ALL = (
    "  STARTUP",
    "    Execute the plate item now; there is no one downstream to hand it to.",
    "    Measure the current state on the item before you change anything.",
    "    When done: close it with a falsifiable reason (before -> after), report",
    "    to your lead (`st inbox -d <lead> ...`), then take the next item.",
    "    Never block your pane on a question: record it on the item, recommend.",
)
# No `st ready`: st has no such verb, and the tracker's ready command is
# deployment-specific. test_every_injected_st_verb_is_real pins this.
_EMPTY = "    Plate empty: `st inbox <you>`, then ask your lead for work."
_BY_ROLE = {
    "lead": ("    As a lead, sweep YOUR OWN plate before feeding reports from the pool.",),
    "administrator": ("    As the administrator, drain stop events and feed idle agents first.",),
}


def startup_lines(role: str, plate_empty: bool, plate_stale: bool = False) -> list[str]:
    """The role's startup instructions. Generic by design: no deployment names,
    because this ships in a public package and every deployment reads it."""
    lines = list(_ALL)
    if plate_stale:
        lines[1] = "    Verify the stale plate item's current status before executing it."
    if plate_empty:
        lines.append(_EMPTY)
    lines += _BY_ROLE.get(role, ())
    return lines


def _run_anchor(root: str, agent: str) -> tuple[int, str]:
    from . import cli
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            rc = cli.main(["--root", root, "anchor", agent])
        except SystemExit as e:  # argparse and friends
            rc = e.code if isinstance(e.code, int) else 1
    return int(rc or 0), out.getvalue()


def main(argv=None) -> int:
    """The SessionStart hook. `<interpreter> -m shantytown.session_anchor [--root R]`."""
    args = list(sys.argv[1:] if argv is None else argv)
    root = ""
    if "--root" in args:
        try:
            root = args[args.index("--root") + 1]
        except IndexError:
            root = ""
    root = root or os.environ.get("SHANTY_ROOT", "")
    agent = os.environ.get("SHANTY_AGENT", "")
    if not root or not agent:
        # A session started outside st: not an error, and not reported — a hook
        # that complains on every non-fleet session start gets removed.
        return 0
    print(render(root, agent))
    return 0


def render(root: str, agent: str) -> str:
    """The same context for hook output and verified launch-time delivery."""
    try:
        rc, text = _run_anchor(root, agent)
    except Exception:  # noqa: BLE001 -- a failed lookup must not hide the launch
        rc, text = 2, ""
    if rc != 0 or not text.strip():
        return (f"{BANNER} {agent}: could not read your plate at session start "
                f"(anchor exit {rc}). Run `st anchor` before you pick up work.")
    m = _ROLE_RE.search(text)
    role = m.group(1) if m else ""
    empty = "ON YOUR PLATE\n    nothing." in text
    stale = "\n      STALE PLATE:" in text
    return "\n".join((BANNER, text.rstrip(), "",
                       "\n".join(startup_lines(role, empty, stale))))


if __name__ == "__main__":
    raise SystemExit(main())
