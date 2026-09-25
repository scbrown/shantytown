"""The shape of the `st` surface, in one importable place.

cli.py wires its parser FROM this table; the hook-side matchers (task_order,
cost) read it to recognise a command an agent typed under EITHER spelling
during the alias window — and they must not import cli.py to do so, because a
PostToolUse hook runs on every tool call and cli.py pulls in the whole package.
tests/test_command_count.py pins this table to the parser and the docstring.
"""
from __future__ import annotations

#: THE SURFACE, declared once (Stiwi, 2026-09-17): six verbs stay top-level and
#: the other twenty-seven live under five groups. `None` marks a verb; a tuple
#: is a group's leaves, in `--help` order. This is the one copy of the count
#: that is code, and every other copy has to agree with it.
SURFACE: dict[str, tuple[str, ...] | None] = {
    "task": None, "go": None, "inbox": None, "crew": None, "anchor": None, "attach": None,
    "work": ("repool", "defer", "cost", "dream", "triage"),
    "agent": ("new", "stop", "harness", "cycle", "advise", "input", "ask", "answer",
              "log", "history", "stats"),
    "fleet": ("start", "tend", "roles", "init", "hold", "window", "dashboard"),
    "repo": ("worktree", "push", "context"),
    "ops": ("doctor", "provision", "subscribe", "help"),
}

#: leaf -> its group. Every key here is ALSO an old top-level spelling, aliased
#: for two releases.
GROUP_OF: dict[str, str] = {leaf: group for group, leaves in SURFACE.items()
                            if leaves for leaf in leaves}


def ungroup(args: list[str]) -> list[str]:
    """`["st", "agent", "stats", ...]` -> `["st", "stats", ...]`; anything else
    unchanged. For a matcher that keys on the LEAF: an agent may type either
    spelling while the old one is aliased, and both mean the same command."""
    if (len(args) >= 3 and SURFACE.get(args[1]) is not None
            and args[2] in SURFACE[args[1]]):
        return [args[0], *args[2:]]
    return list(args)
