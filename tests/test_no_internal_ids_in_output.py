"""`st` must not print an internal ticket id to a stranger.

This repo went public. Citing the bead that motivated a change is CORRECT
internally — it is how the reasoning stays findable — and it is wrong in shipped
output, because a user who reads "cf. aegis-05up" cannot resolve it and we have
told them nothing except that we have a private tracker.

We scrubbed ~200 of these by hand. Within HOURS, new commits put ~15 back, and
one landed in user-facing output. Nobody was careless: nothing told the authors
the repo's status had changed. A scrub is a STATE, and states rot. Only a
mechanism holds — so here is the mechanism.

THE LINE THIS DRAWS, precisely: comments and docstrings are artifacts of the
SOURCE and keep their citations. Strings that reach a terminal are SHIPPED
OUTPUT and do not. This test only ever looks at the second kind.

SCOPE, and why it is not the real rule: the authoritative policy lives in the
knowledge graph and covers hostnames, private addresses and paths as well as
ticket ids. This test is a deliberately NARROW, offline subset of it — ticket
ids in shipped output only. It duplicates a fragment of the rule on purpose,
because a public repo's test suite must not name internal services and must pass
with no network. Two gates with two full copies of a rule is how they drift
apart; a gate that knowingly implements one clause of it, and says so, is not
that. If you are extending this test, extend the graph rule instead.
"""
from __future__ import annotations
import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "shantytown"

# Ticket shapes used by the private tracker. Deliberately literal and local:
# see the SCOPE note above.
TICKET = re.compile(r"\b(?:aegis|hq|gassy|qp)-[a-z0-9]{3,6}\b")


def _user_facing_strings(tree: ast.AST):
    """Every literal that can reach a terminal: argparse help/description/epilog,
    and anything handed to print(). Yields (lineno, text)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        targets = []
        func = node.func
        is_print = isinstance(func, ast.Name) and func.id == "print"
        if is_print:
            targets.extend(node.args)
        for kw in node.keywords:
            if kw.arg in {"help", "description", "epilog"}:
                targets.append(kw.value)
        for t in targets:
            for sub in ast.walk(t):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    yield sub.lineno, sub.value


def test_no_internal_ticket_ids_in_user_facing_output():
    offenders = []
    for path in sorted(SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno, text in _user_facing_strings(tree):
            for m in TICKET.finditer(text):
                offenders.append(f"{path.name}:{lineno}: {m.group(0)} in {text[:70]!r}")
    assert not offenders, (
        "internal ticket id(s) in SHIPPED OUTPUT — a public user cannot resolve "
        "these:\n  " + "\n  ".join(offenders) +
        "\n\nKeep the citation in a comment next to the code; take it out of the "
        "string that gets printed."
    )


def test_the_detector_actually_detects():
    """The positive control. A guard that has never been seen catching anything
    is not evidence — it is a function that returns None. Feed it a known
    offender and require it to fire; otherwise a broken AST walk reads exactly
    like a clean repo."""
    tree = ast.parse(
        'import argparse\n'
        'p = argparse.ArgumentParser()\n'
        'p.add_argument("--x", help="see aegis-1234 for why")\n'
        'print(f"failed, cf. hq-9z1x")\n'
    )
    found = [t for _, t in _user_facing_strings(tree) if TICKET.search(t)]
    assert len(found) == 2, f"the walk missed a planted offender: {found}"
