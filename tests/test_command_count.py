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


def test_the_surface_is_twenty():
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

    Grew to 20 with `input` — the input box as a surface you can ASK. Same
    argument as `tend` above and it lands the same way: this could have been a
    flag on `crew`, but `crew` is a READ over the whole roster and `input`
    targets one agent AND can mutate its buffer (--clear/--dismiss). A
    consequence hidden behind a flag on a read command is a consequence someone
    triggers by running the safe-looking thing. It earns its slot for a second
    reason too: a coordinator about to run the stranded-input SOP needs a verb
    to type, and "run st crew and read the fourth column" is not one.

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

    Grew to 19 with `stats` (aegis-5lwl, PART B of st observability): the query
    surface over the LOCAL capture store (.shanty/stats.sqlite) that the
    PostToolUse/Stop hooks append to — files touched, skills used, tokens per
    agent. It is a command and not a dashboard pane because it answers OFFLINE
    questions (what did kelly touch last night) that the live tier view never
    holds, and it is a command and not a flag on `log` because log reads the
    narrative ledger while stats reads the capture store — two stores, two
    reads, and hiding one behind the other's flag would imply they agree.

    Grew to 20 with `start` — BOOT the town, by token-conservation MODE (Stiwi,
    owner-directed). The declarative launch surface: it takes "the crew I want
    tonight" and makes it true, with an exit code that says whether it did.

    It is not a flag on either neighbour, and both refusals are about a guard that
    is load-bearing WHERE IT IS and wrong here. `new`'s clobber guard REFUSES a
    live session ("never replace a live agent") — correct for one explicit launch,
    and for a boot exactly backwards, since "already up" is the most common
    success. `tend` refuses to respawn an agent it has no launch stamp for
    (aegis-2j2r: another orchestrator's crew), and a cold host has no stamps for
    anyone — loosening that gate to fit a boot would loosen it for the 5-minute
    timer too. So `start` is the declarative, idempotent one: it converges the
    fleet on a named mode, leaves live agents untouched, and never attaches
    (attaching is `attach`, which now launches on demand — the systemd/cron caller
    cannot afford a foreground tmux client).

    Grew to 21 with `init` — the scaffold wizard (Stiwi, owner-directed). Nothing
    created a store: the crew cards came from a hand-authored hierarchy file fed to
    `roles sync`, the settings files were a side effect of `roles set`, the config
    was hand-written, and `roles set` REFUSES an agent with no card — so the first
    instruction to a new user was "edit this JSON".

    It earns a slot rather than becoming `roles sync --interactive` because sync
    PROJECTS an existing authority (the graph, or a hierarchy file) onto cards and
    is idempotent against it, while init ASKS and creates the authority — including
    two artifacts sync has no opinion about at all, the settings files and
    shantytown.toml. Hanging "invent a crew" off the flag of a command whose
    contract is "mirror what is already declared" would make sync's most dangerous
    property (it overwrites cards to match a source) reachable from a prompt.

    SHRANK to 19: the `role` and `project` ALIASES are gone (deprecated
    2026-07-24, deleted after the one-week window). This is the only direction of
    change this file has ever recorded, and it is worth stating why the earlier
    consolidation did not achieve it: `roles set`/`roles sync` subsumed the two
    old spellings while KEEPING them as aliases, and the count stayed put —
    because an alias is a top-level command to argparse, to `st --help`, and to
    anyone reading the surface. Deletion is the lever; consolidation alone was
    not.

    Each command still earns its slot."""
    assert len(_actual_subcommands()) == 20, (
        "the command count changed. If that's intended, update the number here and "
        "the cli.py docstring together — and say why the surface grew in docs/cli.md."
    )


def test_the_deleted_aliases_are_really_gone():
    """The negative control for the deletion above.

    Both old spellings must be UNKNOWN COMMANDS, not silently-accepted ones: an
    alias that still parses keeps the surface at 21 no matter what the docstring
    claims, and this file exists because a number nobody enforces is a comment.
    """
    import pytest
    from shantytown.cli import main

    assert "role" not in _actual_subcommands()
    assert "project" not in _actual_subcommands()
    for gone in (["role", "set", "ellie", "worker"], ["project", "-n"]):
        with pytest.raises(SystemExit) as e:
            main(gone)
        assert e.value.code == 2, "argparse refuses an unknown command with 2"


def test_the_canonical_spellings_still_work():
    """...and the handlers behind them are UNTOUCHED. Only the alias parsers went;
    `roles set` / `roles sync` dispatch to the same functions they always did."""
    assert {"roles"} <= _actual_subcommands()
    assert cli._cmd_role is not None and cli._cmd_project is not None
