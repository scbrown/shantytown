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
