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


def test_the_surface_is_thirteen():
    """A bare number check too, so 'the docs claim N' is itself pinned.

    Grew to 13 with `project` — materialize the crew cards from the graph (the
    quipu-registry projection). Each command still earns its slot."""
    assert len(_actual_subcommands()) == 13, (
        "the command count changed. If that's intended, update the number here and "
        "the cli.py docstring together — and say why the surface grew in docs/cli.md."
    )
