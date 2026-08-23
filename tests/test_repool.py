"""`st repool` — the whole hand-back (aegis-ap4gm fix #1).

The defect: `bd update -a ""` clears the assignee and LEAVES the status at
in_progress, so the item is in no haul, on no plate, and outside `bd ready` —
it silently leaves the system. Repool must do both halves in one verified
write, and must refuse the two states where "back on the board" is a lie:
closed (resurrection) and blocked (a decision served to the next free agent).
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown.dispatch import (Dispatcher, Repool, RepoolRefused, _applied)
from shantytown.files import FilesRegistry, FilesTracker
from shantytown.tmux import NullPanes


class CountingTracker(FilesTracker):
    def __init__(self, root):
        super().__init__(root)
        self.updates = 0

    def update(self, item_id, **fields):
        self.updates += 1
        return super().update(item_id, **fields)


@pytest.fixture
def world(tmp_path: Path):
    reg_dir = tmp_path / "crew"; reg_dir.mkdir()
    (reg_dir / "ellie.json").write_text(json.dumps(
        {"role": "worker", "pane": "%5"}))
    tracker = CountingTracker(tmp_path / "items")
    tracker.update("item-1", title="Restore the den service", status="open")
    tracker.updates = 0
    return Dispatcher(FilesRegistry(reg_dir), tracker, NullPanes()), tracker


def test_repool_resets_status_AND_clears_assignee(world):
    """The whole point: hand-back returns the item to the BOARD, not to limbo."""
    d, tracker = world
    tracker.update("item-1", status="in_progress", assignee="ellie")
    tracker.updates = 0

    r = d.repool("item-1")

    row = tracker.get("item-1")
    assert row.status == "open", "status stayed in_progress — the ap4gm limbo"
    assert not (row.assignee or "").strip()
    assert r.holder == "ellie" and r.was_status == "in_progress"
    assert r.track_attempts == 1, (
        "a landed clear read back as a loss — the _applied clearing case")


def test_repool_recovers_the_orphan_case(world):
    """in_progress with NO assignee — the state `bd update -a \"\"` leaves."""
    d, tracker = world
    tracker.update("item-1", status="in_progress", assignee="")
    tracker.updates = 0

    r = d.repool("item-1")

    assert tracker.get("item-1").status == "open"
    assert r.holder == "" and not r.noop


def test_repool_of_a_CLOSED_item_is_refused_and_writes_nothing(world):
    """Repool must not resurrect finished work — same rule as the serve path."""
    d, tracker = world
    tracker.update("item-1", status="closed", assignee="ellie")
    tracker.updates = 0

    with pytest.raises(RepoolRefused):
        d.repool("item-1")
    assert tracker.updates == 0
    assert tracker.get("item-1").status == "closed"


def test_repool_of_a_BLOCKED_item_is_refused_and_writes_nothing(world):
    """Blocked is a decision; repooling it would put it on `bd ready`."""
    d, tracker = world
    tracker.update("item-1", status="blocked", assignee="ellie")
    tracker.updates = 0

    with pytest.raises(RepoolRefused):
        d.repool("item-1")
    assert tracker.updates == 0
    assert tracker.get("item-1").status == "blocked"


def test_repool_of_an_already_pooled_item_is_a_noop(world):
    d, tracker = world  # setup state: open, unassigned

    r = d.repool("item-1")

    assert r.noop
    assert tracker.updates == 0


def test_repool_dry_run_writes_nothing(world):
    d, tracker = world
    tracker.update("item-1", status="in_progress", assignee="ellie")
    tracker.updates = 0

    r = d.repool("item-1", dry_run=True)

    assert tracker.updates == 0
    assert r.was_status == "in_progress" and r.holder == "ellie"
    assert tracker.get("item-1").status == "in_progress", "dry-run wrote"


def test_applied_clearing_direction():
    """want=\"\" means 'the field is now empty' — None and \"\" both count.
    Before this, an applied clear returned False, earned the silent-loss
    retries, and raised TrackerWriteLost on a write that had landed."""
    assert _applied("", "")
    assert _applied(None, "")
    assert not _applied("ellie", "")
    # the non-empty direction is unchanged
    assert _applied("ellie", "ellie")
    assert _applied("aegis/ellie", "ellie")
    assert not _applied("", "ellie")
    assert not _applied(None, "ellie")
