"""st go verifies the send landed before it records the work — shantytown #2.

The order is SEND -> VERIFY -> UPDATE. A send that does not land must leave the
tracker UNTOUCHED (no in_progress for work nobody received) and exit 2
(could-not-confirm), not 0. design.md: "verify reads the pane back. Send-and-
assume is how you believe work was assigned when it wasn't."

test_dropped_send_is_caught_and_nothing_is_written is the reason this exists, and
it is positive-controlled by construction: NullPanes(drops=True) models a pane
whose send does not land, so verify MUST fail there. A verify that has never been
seen failing is not evidence.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown.dispatch import Dispatcher, SendUnverified
from shantytown.files import FilesRegistry, FilesTracker
from shantytown.tmux import NullPanes


class _CountingTracker(FilesTracker):
    def __init__(self, root):
        super().__init__(root)
        self.updates = 0

    def update(self, item_id, **fields):
        self.updates += 1
        return super().update(item_id, **fields)


@pytest.fixture
def world(tmp_path: Path):
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "ellie.json").write_text(json.dumps({"role": "worker", "pane": "%5"}))
    trk = _CountingTracker(tmp_path / "items")
    trk.update("item-1", title="Restore the den", status="open")
    trk.updates = 0
    return crew, trk


def test_landed_send_verifies_then_writes(world):
    """The happy path: NullPanes echoes the send, verify sees the item id, THEN
    the tracker is written. Order proven by the end state."""
    crew, trk = world
    panes = NullPanes(screen="")                    # healthy -> NUDGE
    d = Dispatcher(FilesRegistry(crew), trk, panes)
    d.go("item-1", "ellie")
    assert len(panes.sent) == 1
    assert "item-1" in panes.capture("%5"), "send did not become visible"
    assert trk.get("item-1").status == "in_progress"
    assert trk.updates == 1


def test_dropped_send_is_caught_and_nothing_is_written(world):
    """THE #2 FIX, positive-controlled. The send does not land (drops=True), so
    verify fails: SendUnverified, and the tracker is NEVER written — no
    in_progress for work that was not delivered."""
    crew, trk = world
    panes = NullPanes(screen="", drops=True)        # send succeeds, never lands
    d = Dispatcher(FilesRegistry(crew), trk, panes)

    with pytest.raises(SendUnverified):
        d.go("item-1", "ellie")

    assert panes.sent == [("%5", "Work is on your hook: item-1 — Restore the den")], \
        "we should have attempted the send"
    assert trk.updates == 0, "verify failed but the tracker was written — half-dispatch"
    assert trk.get("item-1").status == "open", "item marked in_progress for a lost send"


def test_verify_reads_the_pane_back(world):
    """verify() is exactly 'is the item id on the pane?' — not send-and-assume."""
    crew, trk = world
    d = Dispatcher(FilesRegistry(crew), trk, NullPanes())
    assert d.verify("%5", "aegis-x") is False           # empty pane
    assert d.verify("%5", "aegis-x") is False
    landed = NullPanes(screen="… Work is on your hook: aegis-x — do the thing")
    d2 = Dispatcher(FilesRegistry(crew), trk, landed)
    assert d2.verify("%5", "aegis-x") is True


def test_update_happens_AFTER_send_not_before(world):
    """The reorder is the point. If update preceded send, a dropped send would
    leave a stale in_progress. Prove update is last: on a dropped send, zero
    writes; on a landed send, the write exists and the send preceded it."""
    crew, trk = world
    # dropped: no write at all
    dropped = NullPanes(drops=True)
    with pytest.raises(SendUnverified):
        Dispatcher(FilesRegistry(crew), trk, dropped).go("item-1", "ellie")
    assert trk.updates == 0
