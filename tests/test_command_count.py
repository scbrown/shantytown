"""The count is the thesis — so pin it (malcolm doc-defect #2).

cli.py's docstring once said "Ten commands" while the code had eleven (context
landed unannounced) and docs/cli.md said "nine" and "eight" — a three-way drift in
the one repo whose entire pitch is the exact command count. A number nobody enforces
is a comment. This test makes the docstring and the code prove each other: the set
of commands named in the docstring must equal the set of subparsers actually wired.
Add a command without updating the docstring (or vice versa) and this goes red.
"""
from __future__ import annotations
import argparse
import re

import shantytown.cli as cli


def _actual_subcommands() -> set[str]:
    parser = cli.build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def _documented_commands() -> set[str]:
    """Parse the `prime · go · ...` command line(s) from the module docstring.
    Each token's first word is the command name (roles [--check] -> roles,
    role set -> role, doctor [--install] -> doctor)."""
    doc = cli.__doc__ or ""
    names: set[str] = set()
    for line in doc.splitlines():
        # The command-list lines separate commands with `·` and carry NO prose.
        # The justification bullets ALSO start with `·` but each has an em-dash
        # description (`· doctor — ...`); excluding em-dash lines keeps this from
        # scraping a command name out of prose and masking a real drift.
        if "·" not in line or "—" in line:
            continue
        for token in line.split("·"):
            token = token.strip()
            if not token:
                continue
            first = token.split()[0]
            if re.fullmatch(r"[a-z]+", first):
                names.add(first)
    return names


def test_docstring_and_code_agree_on_the_command_set():
    documented = _documented_commands()
    actual = _actual_subcommands()
    assert documented == actual, (
        f"command surface drifted — docstring lists {sorted(documented)} but the "
        f"parser wires {sorted(actual)}. Update BOTH the cli.py docstring and this "
        f"is deliberate friction: the count is the product."
    )


def test_the_surface_is_nineteen():
    """A bare number check too, so 'the docs claim N' is itself pinned.

    Grew to 13 with `project` — materialize the crew cards from the graph (the
    quipu-registry projection).

    Grew to 14 with `tend` — crew supervision, moved off the Gas Town watchdog
    and made native. This one was argued the other way first and lost on a
    specific ground worth keeping: it could have been a flag on `st crew`, and
    that is exactly the objection. `crew` is a READ, and `tend` is the only
    surface in this repo that can create a session and launch an agent. A
    consequence hidden behind a flag on a read command is a consequence someone
    triggers by running the safe-looking thing. The verb gets its own slot so the
    mutation shows up in shell history, in `--help`, and here.

    Grew to 15 with `attach` — attach to a crew member by name. A tool that
    manages the crew but cannot attach to one is missing its most basic verb, and
    the manual path (`tmux -L gt-ae5f35 attach -t shanty-weaver`) leaks the two
    internal details — the socket name and the pane prefix — that st already hides
    in crew/go/tend. It is not a flag on `crew` for the same reason `tend` is not:
    `crew` is a read, and `attach` hands the terminal to a live agent's pane. It
    earns the slot the way go/stop do — a core, frequent operator action with its
    own refusal discipline (unknown or down agent refused by name), and it is
    where "use shanty, not bare tmux" becomes the default: attach goes THROUGH
    shanty (themed) when present, bare tmux only when absent.

    Grew to 16 with `dashboard` — a live, self-refreshing view of ONE admin's
    tier: roster, current work, the REUSED state verdicts, last activity, tallies.
    It is not `crew` with a flag: `crew` is a one-shot flat roster of the whole
    fleet; `dashboard` is tier-scoped, composed (crew + anchor + the event
    ledger), and always-on — the operator keeps it in a second pane. Different
    lifetime, different scope, different composition; it earns its own verb the
    way an observability panel is not a status line.

    Grew to 17 with `subscribe` — watch quipu entity events and route assigned
    workflows to the admin (the events adapter integrations.md sketched, finally
    built first-class on Quipu's cursored transaction log). Owner-directed; the
    count is deliberate friction, not a ceiling. (15-vs-16 note: attach/dashboard
    and subscribe landed on DIVERGED remotes — origin and github each grew a
    disjoint surface off 14, and both sides' "15" claims were true in their own
    world. This merge is where the two worlds reconciled to 17.)

    Grew to 18 with `worktree` — provision an agent's isolated worktree off a
    SHARED project repo. A shared checkout shares its index and HEAD, so two agents
    committing there corrupt each other silently; st gives each its own worktree so
    the shared tree is never the write surface for two writers. It is not a flag on
    another command because it MUTATES the working set (creates a worktree, or
    removes one under --gc) — the same reason tend and attach earn their own slots:
    a consequence hidden behind a flag on a read is a consequence someone triggers
    by running the safe-looking thing. Owner-directed (the worktrees bug).

    Grew to 19 with `stats` (internal-ref, PART B of st observability): the query
    surface over the LOCAL capture store (.shanty/stats.sqlite) that the
    PostToolUse/Stop hooks append to — files touched, skills used, tokens per
    agent. It is a command and not a dashboard pane because it answers OFFLINE
    questions (what did kelly touch last night) that the live tier view never
    holds, and it is a command and not a flag on `log` because log reads the
    narrative ledger while stats reads the capture store — two stores, two
    reads, and hiding one behind the other's flag would imply they agree.

    Each command still earns its slot."""
    assert len(_actual_subcommands()) == 19, (
        "the command count changed. If that's intended, update the number here and "
        "the cli.py docstring together — and say why the surface grew in docs/cli.md."
    )
