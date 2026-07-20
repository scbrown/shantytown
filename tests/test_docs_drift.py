"""The DOCS are pinned to the code, not just cli.py's docstring (GitHub #8).

test_command_count.py already makes the cli.py docstring and the parser prove each
other. That left the actual reader-facing surface — README.md and docs/cli.md —
unenforced, and they drifted exactly as far as you would predict:

  * README advertised `st triage`, which has never been a subcommand.
  * README's badges said 10 commands / 57 tests at 13 commands / 292 tests.
  * docs/cli.md said "Twelve" with thirteen wired, and — the worst one — spelled
    every single one of its 29 examples `shanty <cmd>` when the installed entry
    point is `st`. Every command a reader copied out of the CLI reference was
    uninvokable.

"A count nobody enforces is a comment" was already this repo's line. These tests
extend it from the docstring to the documents, because that is where the reader
actually looks.
"""
from __future__ import annotations
import argparse
import re
import subprocess
import sys
from pathlib import Path

import pytest

import shantytown.cli as cli

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
CLI_MD = ROOT / "docs" / "cli.md"
DOCS = sorted((ROOT / "docs").glob("*.md"))


def _subcommands() -> set[str]:
    parser = cli.build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def _commands_in_block(text: str, fence_contains: str) -> set[str]:
    """Pull `st <cmd>` names out of the fenced block that contains a marker."""
    for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.S):
        if fence_contains in block:
            return {m.group(1) for m in re.finditer(r"^st ([a-z]+)", block, re.M)}
    raise AssertionError(f"no fenced block containing {fence_contains!r}")


# --- the surface listing in each document must BE the surface ----------------

def test_readme_whole_surface_block_lists_exactly_the_wired_commands():
    listed = _commands_in_block(README.read_text(), "← the primer")
    assert listed == _subcommands(), (
        f"README's 'whole surface' block drifted: it lists {sorted(listed)}, the "
        f"parser wires {sorted(_subcommands())}."
    )


def test_cli_md_whole_surface_block_lists_exactly_the_wired_commands():
    listed = _commands_in_block(CLI_MD.read_text(), "st prime")
    assert listed == _subcommands(), (
        f"docs/cli.md's surface block drifted: it lists {sorted(listed)}, the "
        f"parser wires {sorted(_subcommands())}."
    )


# --- no document may advertise a command that does not exist -----------------

# A doc may NAME a command we refuse to have — "No `st handoff`", "if it grows a
# `st convoy`, we've rebuilt the thing we left". Those sentences are the opposite
# of advertising and are load-bearing prose; the test must tell them apart from a
# promise. Disavowal is recognised from the line, not from a hardcoded allowlist,
# so a NEW refusal needs no test edit but a new PROMISE still fails.
_DISAVOWS = re.compile(r"\bNo `st|if it grows|we've rebuilt|never a subcommand", re.I)


def test_no_doc_advertises_a_command_the_cli_does_not_have():
    """THE `st triage` TEST. triage is a library module, never a subcommand, and
    the README sold it as one from the first commit."""
    wired = _subcommands()
    offenders: list[str] = []
    for doc in [README, *DOCS]:
        for line in doc.read_text().splitlines():
            if _DISAVOWS.search(line):
                continue
            for m in re.finditer(r"`st ([a-z]+)", line):
                if m.group(1) not in wired:
                    offenders.append(f"{doc.relative_to(ROOT)}: `st {m.group(1)}`")
    assert not offenders, (
        "docs advertise commands the CLI does not wire: " + ", ".join(sorted(set(offenders)))
        + ". Either build it or stop promising it."
    )


# --- the binary is `st`; `shanty` is somebody else's command -----------------

def test_no_doc_tells_the_reader_to_run_shanty():
    """`shanty` is Stiwi's tmux wrapper — a DIFFERENT program that is not us and
    is not on PATH here. Naming a command `shanty <cmd>` is not a typo; it sends
    the reader to another tool. vision.md may still MENTION shanty as a peer
    project; what it may not do is present it as our invocation.
    """
    entry = (ROOT / "pyproject.toml").read_text()
    assert re.search(r"^st = ", entry, re.M), "the entry point is no longer `st`; fix these tests"

    offenders = []
    for doc in [README, *DOCS]:
        for m in re.finditer(r"shanty ([a-z]+)", doc.read_text()):
            if m.group(1) in _subcommands():
                offenders.append(f"{doc.relative_to(ROOT)}: shanty {m.group(1)}")
    assert not offenders, (
        "docs invoke `shanty <cmd>`; the installed binary is `st`: " + ", ".join(offenders)
    )


# --- the badges are claims, so they get checked too --------------------------

def test_readme_command_badge_matches_the_wired_count():
    m = re.search(r"badge/commands-(\d+)-", README.read_text())
    assert m, "the README command-count badge is gone"
    assert int(m.group(1)) == len(_subcommands()), (
        f"README badge claims {m.group(1)} commands; the parser wires {len(_subcommands())}."
    )


def test_readme_versus_table_matches_the_wired_count():
    m = re.search(r"\| Commands \| ~110 \| \*\*(\d+)\*\* \|", README.read_text())
    assert m, "the Versus Gas Town command row is gone"
    assert int(m.group(1)) == len(_subcommands())


def test_readme_test_badge_matches_the_real_collected_count():
    """The badge said 57 while the suite was 292 — a 5x-stale number on the repo
    whose pitch is 'a check that cannot fail is not a check'.

    Collected, not run: --collect-only does not execute tests, so this cannot
    recurse. If pytest can't be re-invoked we SKIP rather than pass — an unrun
    check must not report green (this repo's own rule, applied to its own test).
    """
    m = re.search(r"badge/tests-(\d+)%20passing", README.read_text())
    assert m, "the README test-count badge is gone"
    claimed = int(m.group(1))

    try:
        out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"],
                             cwd=ROOT, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:      # pragma: no cover
        pytest.skip(f"could not re-invoke pytest to count tests: {e}")
    got = re.search(r"^(\d+) tests? collected", out.stdout, re.M)
    if not got:                                            # pragma: no cover
        pytest.skip(f"could not parse a collection count from pytest: {out.stdout[-300:]!r}")

    actual = int(got.group(1))
    assert claimed == actual, (
        f"README badge claims {claimed} tests; the suite collects {actual}. "
        f"Update the badge in the same commit that changes the suite."
    )


def test_cli_md_stated_count_matches_the_wired_count():
    words = {8: "Eight", 9: "Nine", 10: "Ten", 11: "Eleven", 12: "Twelve",
             13: "Thirteen", 14: "Fourteen", 15: "Fifteen"}
    n = len(_subcommands())
    assert n in words, "add the number word and update docs/cli.md"
    text = CLI_MD.read_text()
    assert re.search(rf"^{words[n]}\.", text, re.M), (
        f"docs/cli.md does not open its count paragraph with {words[n]!r} for "
        f"{n} wired commands."
    )
