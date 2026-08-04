"""The two plate readers must agree — this is the test whose ABSENCE let them drift.

The two-implementation rule (docs/adapters.md) says every adapter ships two impls
so the second proves the first didn't leak. That only works if something asserts
they AGREE. Nothing did for plate(), and they diverged: files.plate returned any
non-closed assigned item, beads.plate filtered to hooked/in_progress, so the same
logical dataset (an open-assigned bead) produced an item from one backend and None
from the other. malcolm hit it live. This test is the leak detector
that should have existed: build the identical dataset in both backends, assert the
plate is the same id (or both None). If a future edit moves one reader's semantics
and not the other's, this goes red.
"""
from __future__ import annotations
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from shantytown.beads import BeadsTracker, plate as beads_plate
from shantytown.files import FilesTracker, plate as files_plate


class FakeBd(BeadsTracker):
    def __init__(self, rows):
        super().__init__()
        self._rows = rows

    def _bd(self, *args):
        return SimpleNamespace(returncode=0, stdout=json.dumps(self._rows), stderr="")


def _files_backend(tmp_path: Path, rows) -> FilesTracker:
    t = FilesTracker(tmp_path / "items")
    for r in rows:
        t.update(r["id"], title=r.get("title", ""),
                 status=r["status"], assignee=r["assignee"])
    return t


# The same logical dataset, described once, run through both backends.
DATASETS = {
    "open_assigned_only": [
        {"id": "a", "assignee": "arnold", "status": "open"},
    ],
    "mixed_precedence": [
        {"id": "a", "assignee": "arnold", "status": "open"},
        {"id": "z", "assignee": "arnold", "status": "in_progress"},
        {"id": "m", "assignee": "arnold", "status": "hooked"},
    ],
    "all_closed": [
        {"id": "a", "assignee": "arnold", "status": "closed"},
    ],
    "none_mine": [
        {"id": "a", "assignee": "someone-else", "status": "open"},
    ],
    "tie_by_id": [
        {"id": "z", "assignee": "arnold", "status": "open"},
        {"id": "a", "assignee": "arnold", "status": "open"},
    ],
    # BLOCKED is not workable by definition — serving it CYCLES the agent
    # (internal-ref). Both readers must exclude it, or the one that does not
    # becomes the leak.
    "blocked_only": [
        {"id": "a", "assignee": "arnold", "status": "blocked"},
    ],
    "blocked_must_not_outrank_workable": [
        {"id": "a", "assignee": "arnold", "status": "blocked"},
        {"id": "z", "assignee": "arnold", "status": "open"},
    ],
    # DEFERRED arrives by the same road as blocked (internal-ref): it is a
    # DECISION not to work it now, so serving it cycles the agent exactly the
    # same way. Both readers must exclude it, or the one that does not is the
    # leak — which is the failure this file's whole existence is about.
    "deferred_only": [
        {"id": "a", "assignee": "arnold", "status": "deferred"},
    ],
    "deferred_must_not_outrank_workable": [
        {"id": "a", "assignee": "arnold", "status": "deferred"},
        {"id": "z", "assignee": "arnold", "status": "open"},
    ],
    # Both unworkable statuses at once, with a workable item sorting LAST by id.
    # The live shape: ellie's plate, where a deferred bead outranked her whole
    # haul on id order alone.
    "unworkable_mix_with_workable_last": [
        {"id": "a", "assignee": "arnold", "status": "deferred"},
        {"id": "b", "assignee": "arnold", "status": "blocked"},
        {"id": "z", "assignee": "arnold", "status": "open"},
    ],
}


@pytest.mark.parametrize("name", list(DATASETS))
def test_both_backends_return_the_same_plate(tmp_path, name):
    rows = DATASETS[name]
    fp = files_plate(_files_backend(tmp_path, rows), "arnold")
    bp = beads_plate(FakeBd(rows), "arnold")
    fp_id = fp.id if fp else None
    bp_id = bp.id if bp else None
    assert fp_id == bp_id, f"{name}: files->{fp_id} but beads->{bp_id} — the readers disagree"


def test_the_regression_itself_open_assigned_is_not_None_in_either(tmp_path):
    """The specific case malcolm hit: neither backend may drop open-assigned to None."""
    rows = DATASETS["open_assigned_only"]
    assert files_plate(_files_backend(tmp_path, rows), "arnold") is not None
    assert beads_plate(FakeBd(rows), "arnold") is not None


# --- blocked must never reach a plate (internal-ref) ------------------------
#
# MEASURED 2026-08-02: 16 beads with status `blocked` were assigned, ALL 16, and
# the plate reader served them. That does not just waste a turn, it CYCLES the
# agent — plate hands over an unworkable item, the agent burns a turn finding
# out, stops, and the next stop event hands it straight back. Observed on four
# agents in one evening. It also makes the agent read as BUSY to the
# coordinator, so the fleet reports capacity it does not have.
#
# `bd ready` already excludes blocked, so hauls/dispatchable/Rule Zero were
# clean; the PLATE READERS were the only leak, which is why the fix and this
# test live here.

def test_a_blocked_bead_never_becomes_a_plate_item(tmp_path):
    rows = [{"id": "a", "assignee": "arnold", "status": "blocked"}]
    assert files_plate(_files_backend(tmp_path, rows), "arnold") is None
    assert beads_plate(FakeBd(rows), "arnold") is None


def test_a_workable_bead_is_still_served_alongside_a_blocked_one(tmp_path):
    """The discrimination control. Without it the exclusion could be returning
    None for everything and both assertions above would still pass."""
    rows = [{"id": "a", "assignee": "arnold", "status": "blocked"},
            {"id": "z", "assignee": "arnold", "status": "open"}]
    fp = files_plate(_files_backend(tmp_path, rows), "arnold")
    bp = beads_plate(FakeBd(rows), "arnold")
    assert fp is not None and bp is not None, "excluded the workable item too"
    assert fp.id == "z" and bp.id == "z", "served the BLOCKED bead over the open one"


# --- deferred must never reach a plate either (internal-ref) ----------------
#
# MEASURED 2026-08-04 on the live store: 41 deferred beads, 38 of them ASSIGNED,
# and `st anchor ellie` served aegis-dg5 (deferred) ahead of eight workable items
# in her haul — because plate precedence is (hooked, in_progress, everything-
# else) then lowest id, and "everything else" silently included deferred.
#
# Same scope as blocked, measured the same way: `bd ready` already excludes
# deferred (394 ready, all 394 `open`), so hauls / dispatchable / Rule Zero were
# clean and the PLATE READERS were again the only leak.

def test_a_deferred_bead_never_becomes_a_plate_item(tmp_path):
    rows = [{"id": "a", "assignee": "arnold", "status": "deferred"}]
    assert files_plate(_files_backend(tmp_path, rows), "arnold") is None
    assert beads_plate(FakeBd(rows), "arnold") is None


def test_a_workable_bead_is_still_served_alongside_a_deferred_one(tmp_path):
    """The discrimination control, same as blocked's. Without it the exclusion
    could be returning None for everything and the assertion above still passes."""
    rows = [{"id": "a", "assignee": "arnold", "status": "deferred"},
            {"id": "z", "assignee": "arnold", "status": "open"}]
    fp = files_plate(_files_backend(tmp_path, rows), "arnold")
    bp = beads_plate(FakeBd(rows), "arnold")
    assert fp is not None and bp is not None, "excluded the workable item too"
    assert fp.id == "z" and bp.id == "z", "served the DEFERRED bead over the open one"


def test_the_live_shape_deferred_outranking_a_whole_haul_on_id_order(tmp_path):
    """ellie's actual plate: a deferred bead sorting first by id, workable work
    behind it. The regression this bead was filed for, in one dataset."""
    rows = [{"id": "aegis-dg5", "assignee": "ellie", "status": "deferred"}] + [
        {"id": f"aegis-w{i}", "assignee": "ellie", "status": "open"} for i in range(8)
    ]
    fp = files_plate(_files_backend(tmp_path, rows), "ellie")
    bp = beads_plate(FakeBd(rows), "ellie")
    assert fp is not None and bp is not None
    assert fp.id == "aegis-w0" and bp.id == "aegis-w0", (
        f"served the deferred bead over the haul: files->{fp.id} beads->{bp.id}")


def test_the_readers_share_ONE_unworkable_predicate(tmp_path):
    """THE ANTI-DRIFT ASSERTION, not a behaviour test.

    The exclusion is one composed predicate (inbox.is_unworkable) rather than a
    chain of `not is_blocked and not is_deferred` at each reader, precisely so a
    THIRD unworkable status is one edit instead of four. This asserts both
    readers still route through it: monkeypatching the seam must move BOTH, and
    if a future edit inlines the check at one reader, this goes red there rather
    than waiting for the fleet to serve an unworkable plate again.
    """
    import shantytown.beads as beads_mod
    import shantytown.files as files_mod

    rows = [{"id": "a", "assignee": "arnold", "status": "open"}]
    everything_unworkable = lambda status: True   # noqa: E731
    for mod in (beads_mod, files_mod):
        assert mod.is_unworkable is not None, f"{mod.__name__} does not import the seam"

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(beads_mod, "is_unworkable", everything_unworkable)
        monkey.setattr(files_mod, "is_unworkable", everything_unworkable)
        assert files_plate(_files_backend(tmp_path, rows), "arnold") is None, (
            "files.plate does not go through inbox.is_unworkable")
        assert beads_plate(FakeBd(rows), "arnold") is None, (
            "beads.plate does not go through inbox.is_unworkable")
    finally:
        monkey.undo()
    # ...and the control: with the seam restored, the same row IS served.
    assert files_plate(_files_backend(tmp_path, rows), "arnold") is not None
    assert beads_plate(FakeBd(rows), "arnold") is not None
