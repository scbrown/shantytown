"""beads.plate() — the beads sibling of files.plate() (aegis-gqr8 ruling).

RULING (arnold): "what's on my plate" is a per-backend PLATE READER injected into
prime, NOT a third Tracker method. The two-function Tracker stays; ellie's test
and the swap depend on it. malcolm's mine() broke both and was reverted. This
tests the beads reader matches files semantics: at most one, None when empty, and
RAISE (not empty) when it could not look.
"""
from __future__ import annotations
import json
from types import SimpleNamespace

import pytest

from shantytown.beads import BeadsTracker, plate


class FakeBd(BeadsTracker):
    """A BeadsTracker whose _bd returns canned JSON."""
    def __init__(self, rows, rc=0, stderr=""):
        super().__init__()
        self._rows, self._rc, self._stderr = rows, rc, stderr

    def _bd(self, *args):
        return SimpleNamespace(returncode=self._rc,
                               stdout=json.dumps(self._rows) if self._rc == 0 else "",
                               stderr=self._stderr)


def test_returns_none_when_nothing_active():
    t = FakeBd([{"id": "a", "assignee": "arnold", "status": "closed"}])
    assert plate(t, "arnold") is None


def test_returns_the_one_hooked_item():
    t = FakeBd([
        {"id": "a", "assignee": "arnold", "status": "in_progress", "title": "A"},
        {"id": "b", "assignee": "arnold", "status": "hooked", "title": "B"},
    ])
    p = plate(t, "arnold")
    assert p is not None and p.id == "b", "hooked should outrank in_progress"


def test_only_my_items():
    t = FakeBd([{"id": "a", "assignee": "someone-else", "status": "hooked"}])
    assert plate(t, "arnold") is None


def test_matches_qualified_or_short_name():
    t = FakeBd([{"id": "a", "assignee": "beads_aegis/crew/arnold", "status": "hooked", "title": "X"}])
    assert plate(t, "beads_aegis/crew/arnold").id == "a"


def test_at_most_one_even_with_many():
    rows = [{"id": f"i{n}", "assignee": "arnold", "status": "in_progress"} for n in range(5)]
    p = plate(FakeBd(rows), "arnold")
    assert p is not None  # a single WorkItem, never a list — can't grow to a dashboard


def test_deterministic_tie_break():
    rows = [{"id": "z", "assignee": "arnold", "status": "hooked"},
            {"id": "a", "assignee": "arnold", "status": "hooked"}]
    assert plate(FakeBd(rows), "arnold").id == "a"  # lowest id, stable across runs


def test_bd_failure_RAISES_not_returns_none():
    """could-not-look != empty-plate. Raise so prime surfaces exit 2, never
    reports 'nothing on your plate' when it simply could not ask."""
    t = FakeBd([], rc=1, stderr="connection refused")
    with pytest.raises(RuntimeError, match="bd list failed"):
        plate(t, "arnold")
