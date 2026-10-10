"""`st work defer` must be able to write a MACHINE-TESTABLE resume condition, and must
never create an invisible deferral silently (aegis-bqcjws).

THE DEFECT. `st work defer` wrote status + blocker label + a prose reason into notes.
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

from shantytown.dispatch import DeferRefused, Dispatcher, TrackerWriteLost
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


def test_without_condition_refuses_without_writing(world):
    d, tracker = world
    before = tracker._path("item-1").read_bytes()
    with pytest.raises(DeferRefused, match="resume condition"):
        d.defer("item-1", "parked", "no condition given")
    assert tracker._path("item-1").read_bytes() == before


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


def test_existing_condition_is_preserved(world):
    d, tracker = world
    tracker.update("item-1", defer_until="2026-12-01")
    r = d.defer("item-1", "human", "waiting on a person")
    assert r.condition == "2026-12-01"
    assert tracker.get("item-1").status == "deferred"


def test_a_resume_when_MARKER_counts_as_a_condition(world):
    """The warning must not fire at an author who supplied the marker form the
    warning itself recommends.

    Found by using the feature the day it shipped: `st work defer` printed
    "NO RESUME CONDITION ... add a `resume_when: closed:<id>` line" at a deferral
    whose reason already carried exactly that line — and the deferral sweeper was
    QUIET about that same bead, so the tool and the sweeper disagreed about one
    deferral. A warning that fires at someone who already complied teaches the
    reader to ignore it, which costs more than never having warned.
    """
    d, tracker = world
    r = d.defer("item-1", "human", "resume_when: closed:aegis-u6mdxf\n\nblocked on the window")
    assert r.condition == "resume_when closed:aegis-u6mdxf", (
        "a sweeper-readable marker in the notes IS a machine-testable resume "
        f"condition; got {r.condition!r}")


def test_the_marker_is_parsed_with_the_SWEEPERS_regex(world):
    """One vocabulary, not two. If these drift, the tool and the sweeper
    disagree about which deferrals are visible — which is the whole defect."""
    from shantytown.deferrals import _CONDITION
    d, tracker = world
    d.defer("item-1", "human", "resume_when = date: 2026-12-01")
    got = tracker.get("item-1")
    assert _CONDITION.search(got.notes or ""), "sweeper must see it too"


def test_redefer_twice_replaces_marker_and_preserves_history(world):
    from shantytown.deferrals import parse_condition
    d, tracker = world
    for day in ("20", "21"):
        stamp = f"2026-10-{day}T17:00:00Z"
        d.defer("item-1", "external", f"resume_when: date:{stamp}\nActual event {day}", until=stamp)
    row = tracker.get("item-1")
    assert row.notes.count("resume_when:") == 1
    assert parse_condition(row.notes).render() == "date:2026-10-21T17:00:00Z"
    assert row.defer_until == "2026-10-21T17:00:00Z"
    assert "historical resume condition: date:2026-10-20T17:00:00Z" in row.notes
    assert "Actual event 20" in row.notes and "Actual event 21" in row.notes

def test_until_only_replaces_malformed_and_multiple_old_markers(world):
    from shantytown.deferrals import parse_condition
    d, tracker = world
    tracker.update("item-1", notes="Original prose\nresume_when: human:decision\nresume-when = closed:old\nresume when: malformed")
    d.defer("item-1", "external", "Real event requires judgement", until="2026-10-21T17:00:00Z")
    row = tracker.get("item-1")
    assert row.notes.count("resume_when:") == 1
    assert parse_condition(row.notes).render() == "date:2026-10-21T17:00:00Z"
    assert "Original prose" in row.notes and "human:decision" in row.notes
    assert "closed:old" in row.notes and "malformed" in row.notes

def test_duplicate_markers_landing_is_not_confirmed(world):
    d, tracker = world
    real = tracker.update
    def duplicate(item_id, **fields):
        real(item_id, **fields)
        if "defer_reason" in fields:
            row = tracker.get(item_id)
            real(item_id, notes=row.notes + "\nresume_when: date:2026-10-21T17:00:00Z")
    tracker.update = duplicate
    with pytest.raises(TrackerWriteLost, match="resume_when"):
        d.defer("item-1", "external", "real event", until="2026-10-21T17:00:00Z")
    assert tracker.get("item-1").status == "open"


def test_prose_only_redefer_preserves_existing_bead_gate_with_date_backstop(world):
    from shantytown.deferrals import parse_condition
    d, tracker = world
    tracker.update('item-1', notes='resume_when: closed:gate-1', defer_until='2026-10-21T17:00:00Z')
    d.defer('item-1', 'bead', 'The real bead gate still applies')
    assert parse_condition(tracker.get('item-1').notes).render() == 'closed:gate-1'
    assert tracker.get('item-1').notes.count('resume_when:') == 1

def test_prose_only_redefer_cannot_choose_between_multiple_old_markers(world):
    d, tracker = world
    tracker.update('item-1', notes='resume_when: closed:gate-1\nresume_when: closed:gate-2', defer_until='2026-10-21T17:00:00Z')
    before = tracker._path('item-1').read_bytes()
    with pytest.raises(DeferRefused, match='multiple resume markers'):
        d.defer('item-1', 'bead', 'Still waiting')
    assert tracker._path('item-1').read_bytes() == before


def test_new_until_overrides_a_date_marker_copied_in_reason(world):
    from shantytown.deferrals import parse_condition
    d, tracker = world
    d.defer('item-1', 'external', 'resume_when: date:2026-10-20T17:00:00Z\nOld event', until='2026-10-21T17:00:00Z')
    row = tracker.get('item-1')
    assert parse_condition(row.notes).render() == 'date:2026-10-21T17:00:00Z'
    assert row.notes.count('resume_when:') == 1
    assert 'date:2026-10-20T17:00:00Z' in row.notes

def test_new_date_marker_updates_an_existing_deadline(world):
    from shantytown.deferrals import parse_condition
    d, tracker = world
    tracker.update('item-1', notes='resume_when: date:2026-10-20T17:00:00Z', defer_until='2026-10-20T17:00:00Z')
    d.defer('item-1', 'external', 'resume_when: date:2026-10-21T17:00:00Z\nNew event')
    row = tracker.get('item-1')
    assert row.defer_until == '2026-10-21T17:00:00Z'
    assert parse_condition(row.notes).render() == 'date:2026-10-21T17:00:00Z'
