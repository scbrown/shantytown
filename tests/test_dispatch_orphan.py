"""Dispatching an ORPHANED in_progress item says so (aegis-ap4gm).

sattler's framing, and it is the whole design: **the assignee guard is not weak
here, it is BYPASSED.** It reads

    if not reassign and holder and holder != agent_name: raise AlreadyAssigned

so an EMPTY assignee never conflicts and never refuses. An item that is
in_progress with nobody on it is not ordinary — something started it and it was
re-pooled (`bd update -a ""` clears the assignee and leaves the status) — but it
arrives looking exactly like fresh work.

Measured 2026-08-05: 7 of 34 in_progress beads were unassigned. One was
dispatched, `st go` accepted SILENTLY, and it ended safely only because the
receiving agent refused it on its premise. No mechanism caught it.

CARRIED, NOT RAISED. Dispatching an orphan is the REPAIR — it acquires an owner —
so refusing would block the remedy. What was missing was the fact reaching a
human and the agent, which is the same trade `unreadable_deps` already makes.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown.dispatch import Dispatcher
from shantytown.files import FilesRegistry, FilesTracker
from shantytown.tmux import NullPanes


@pytest.fixture
def world(tmp_path: Path):
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "ellie.json").write_text(json.dumps({"role": "worker", "pane": "%5"}))

    def build(status="open", assignee=None):
        trk = FilesTracker(tmp_path / "items")
        trk.update("item-1", title="Restore the den", status=status,
                   **({"assignee": assignee} if assignee else {}))
        return Dispatcher(FilesRegistry(crew), trk, NullPanes(screen="")), trk

    return build


def test_an_orphaned_in_progress_item_is_FLAGGED_not_refused(world):
    """The repair must still happen — it acquires an owner — but not silently."""
    d, _ = world(status="in_progress", assignee=None)
    p = d.plan("item-1", "ellie")
    assert p.orphaned_in_progress is True


def test_the_RECEIVING_AGENT_is_told_it_is_resuming(world):
    """The agent is the one who cannot otherwise tell: the payload for an orphan
    is byte-identical to a fresh dispatch. It ended safely once tonight only
    because a human-grade reader questioned the premise; that is not a control."""
    d, _ = world(status="in_progress", assignee=None)
    p = d.plan("item-1", "ellie")
    assert "RESUMING work somebody started and handed back" in p.text
    assert "Read its comments before acting" in p.text


def test_the_dry_run_preview_names_the_BYPASS_not_just_the_state(world):
    """An operator reading the preview needs to know WHY nothing else warned —
    otherwise the sane inference is that the guards looked and were content."""
    d, _ = world(status="in_progress", assignee=None)
    r = d.plan("item-1", "ellie").render()
    assert "RESUME an ORPHAN" in r
    assert "keys on a field that is empty" in r


def test_an_ORDINARY_open_item_is_not_flagged(world):
    """The discriminating control. If this ever trips, the warning is
    unconditional and becomes the noise it was written to prevent."""
    d, _ = world(status="open", assignee=None)
    p = d.plan("item-1", "ellie")
    assert p.orphaned_in_progress is False
    assert "RESUMING" not in p.text
    assert "ORPHAN" not in p.render()


def test_an_in_progress_item_WITH_an_owner_is_not_an_orphan(world):
    """in_progress alone is not the defect — it is in_progress with NOBODY. A
    re-dispatch to the existing holder (the `st cycle` path) must stay quiet."""
    d, _ = world(status="in_progress", assignee="ellie")
    p = d.plan("item-1", "ellie")
    assert p.orphaned_in_progress is False
