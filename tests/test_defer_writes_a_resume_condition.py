"""`st defer` must be able to write a MACHINE-TESTABLE resume condition, and must
never create an invisible deferral silently (aegis-bqcjws).

THE DEFECT. `st defer` wrote status + blocker label + a prose reason into notes.
The deferral SWEEPER keys off `defer_until` (or a `resume_when:` marker), so a
defer that set neither produced a bead that is off every automated path at once:
feeders skip status=deferred, and the sweeper has nothing to test. st reported
success, truthfully, about the fields it did write — which is why this went
unnoticed: nobody was doing anything wrong.

101 of 112 deferrals were in that state when the sweeper was added (aegis-hm8994).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shantytown.dispatch import Dispatcher, TrackerWriteLost
from shantytown.files import FilesRegistry, FilesTracker
from shantytown.tmux import NullPanes


@pytest.fixture
def world(tmp_path: Path):
    tracker = FilesTracker(tmp_path / "items")
    tracker.update("item-1", title="a parked thing", status="open",
                   assignee="kelly")
    return Dispatcher(FilesRegistry(tmp_path / "crew"), tracker, NullPanes()), tracker


def test_until_writes_the_STRUCTURED_field_and_verifies_it(world):
    d, tracker = world
    r = d.defer("item-1", "parked", "re-evaluate after the window", until="2026-09-20")

    raw = json.loads(tracker._path("item-1").read_text())
    assert raw["defer_until"] == "2026-09-20", (
        "the resume condition must reach the structured field the sweeper reads")
    assert raw["status"] == "deferred"
    assert r.condition == "2026-09-20"
    # and the item reads back through the PROTOCOL, not just off disk
    assert tracker.get("item-1").defer_until == "2026-09-20"


def test_without_until_the_deferral_is_conditionless_and_SAYS_SO(world):
    """The prose-only defer still works — it must not start refusing — but the
    result carries the fact, so the CLI can warn instead of passing silently."""
    d, tracker = world
    r = d.defer("item-1", "parked", "no condition given")
    assert r.condition == ""
    assert json.loads(tracker._path("item-1").read_text()).get("defer_until") in (None, "")


def test_a_defer_until_THAT_NEVER_LANDS_is_caught_by_read_back(world):
    """THE ARM THAT MAKES THE OTHERS MEAN ANYTHING.

    The verification loop used to confirm status and label ONLY, so a condition
    that silently failed to write reported SUCCESS. This tracker accepts the
    write and drops the field — exactly the shape br's notes-overwrite protection
    produced — and the defer must now FAIL LOUDLY rather than report success.
    """
    d, tracker = world

    real_update = tracker.update

    def drops_the_condition(item_id, **fields):
        fields.pop("defer_until", None)          # silently lose it
        return real_update(item_id, **fields)

    tracker.update = drops_the_condition

    with pytest.raises(TrackerWriteLost) as e:
        d.defer("item-1", "parked", "reason", until="2026-09-20")
    assert "defer_until" in str(e.value) or "defer_until" in repr(e.value.__dict__)


def test_the_status_and_label_still_verify_when_no_condition_is_asked_for(world):
    """CONTROL: the new check must not make ordinary prose defers fail."""
    d, tracker = world
    r = d.defer("item-1", "human", "waiting on a person")
    assert r.track_attempts == 1
    assert tracker.get("item-1").status == "deferred"
