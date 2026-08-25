"""Lint: an adapter must not return a bare collection where a partial read is possible.

WHY A LINT AND NOT A REFACTOR. Eight issues share one shape — a function that
cannot distinguish "no" from "could not look" returns the first one — and the
repo had the right instinct four separate times, applied ad hoc by whoever
remembered. Remembering is the part that failed. `shantytown.answer.Answer` makes
the distinction expressible; this test makes forgetting it *visible*, which is
the half that keeps it true after everyone has moved on.

WHY IT IS A RATCHET AND NOT A WALL. There are 13 pre-existing sites. A lint that
goes red on all of them on day one is a lint someone deletes on day two, and then
the rule is gone — so the existing sites are BASELINED with a reason each, and
this test fails only on:

  * a NEW adapter method returning a bare collection  (the actual guard), and
  * a baselined entry that no longer exists           (so the baseline shrinks
                                                       honestly as sites adopt
                                                       Answer, instead of rotting)

The second direction matters as much as the first. A baseline nobody prunes
becomes a list of things everyone assumes are fine.

SCOPE: adapters only — the boundary where an external read happens and a partial
result is therefore possible. Pure/local helpers are excluded by rule rather than
by list (leading underscore), because `Tmux._cmd() -> list[str]` builds an argv
and can no more be "partial" than a string can.
"""
from __future__ import annotations

import ast
import pathlib

PKG = pathlib.Path(__file__).resolve().parent.parent / "shantytown"

# A class is an adapter if it implements one of the protocol surfaces (or is the
# protocol itself). Name-suffix matching is the backstop for impls that do not
# define the method directly.
PROTOCOL_METHODS = {"all", "relevant", "weigh", "since", "plate"}
ADAPTER_SUFFIXES = ("Registry", "Tracker", "Context", "Ranker", "Source",
                    "Adapter", "Stops", "Hierarchy")

BARE = ("list", "dict", "set", "frozenset")

# Pre-existing sites, each with the reason it is not yet an Answer. Delete a line
# when the site adopts Answer — this test will tell you to.
BASELINE: set[tuple[str, str, str]] = set()


def _adapter_methods_returning_bare() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for path in sorted(PKG.glob("*.py")):
        tree = ast.parse(path.read_text())
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            methods = [m for m in cls.body
                       if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            names = {m.name for m in methods}
            is_adapter = bool(names & PROTOCOL_METHODS) or cls.name.endswith(ADAPTER_SUFFIXES)
            if not is_adapter:
                continue
            for m in methods:
                if m.name.startswith("_") or m.returns is None:
                    continue
                if ast.unparse(m.returns).split("[")[0].strip() in BARE:
                    found.add((path.name, cls.name, m.name))
    return found


def test_no_new_bare_collection_returning_adapters():
    """The guard. A new adapter method that returns a bare collection cannot say
    "I only saw part of it", so it will eventually report a partial read as a
    finding — the exact bug behind all eight issues."""
    new = _adapter_methods_returning_bare() - BASELINE
    assert not new, (
        "These adapter methods return a bare collection, so a partial read is "
        "indistinguishable from an empty one:\n"
        + "\n".join(f"  {m}::{c}.{f}()" for m, c, f in sorted(new))
        + "\n\nReturn shantytown.answer.Answer[...] instead: Answer.complete_read(v, how=…) "
          "when the read covered the whole search space, Answer.capped(v, how=…, caveat=…) "
          "when it did not. If this genuinely cannot be partial, add it to BASELINE "
          "with the reason."
    )


def test_baseline_has_no_stale_entries():
    """The other direction. When a site adopts Answer, its baseline line must go,
    or the baseline slowly becomes a list of things everyone assumes are fine."""
    stale = BASELINE - _adapter_methods_returning_bare()
    assert not stale, (
        "These are baselined but no longer return a bare collection — they were "
        "fixed. Delete them from BASELINE:\n"
        + "\n".join(f"  {m}::{c}.{f}()" for m, c, f in sorted(stale))
    )


def test_the_lint_can_actually_see_something(tmp_path, monkeypatch):
    """A detector that finds nothing passes for the wrong reason forever. If the
    AST walk breaks, or the adapter heuristic stops matching, both tests above go
    green while checking nothing — the same-output-two-worlds failure this whole
    module is about, rebuilt inside its own guard."""
    fixture = tmp_path / "known_positive.py"
    fixture.write_text(
        "class BrokenRegistry:\n"
        "    def all(self) -> list[str]:\n"
        "        return []\n")
    monkeypatch.setattr(pathlib.Path, "glob", lambda self, pattern: [fixture])
    found = _adapter_methods_returning_bare()
    assert ("known_positive.py", "BrokenRegistry", "all") in found, (
        "the known-positive control is missing; the walk or heuristic changed")
