"""A deferral blocked by TWO things must not be silently recorded as blocked by
one (aegis-4xwfzw).

THE DEFECT, measured 2026-09-16 on aegis-nrajcw. `resume_when` holds a single
`<kind>:<arg>`, and `parse_condition` takes the FIRST match over
`notes + reason`. Two consequences, and only the first was reported:

  1. A reason naming two blockers keeps ONE of them in the field. A tool reads
     one blocker and considers the bead resumable while the other is open; a
     human reads two. Nothing warned.
  2. Worse, and not in the original report: notes ACCUMULATE, so a bead deferred
     BEFORE carries its old marker EARLIER in the notes and that one wins. On
     nrajcw the author's reason named `closed:aegis-mzdcm0` and st recorded the
     previous deferral's `closed:aegis-nt4rap`, echoing it back as success. The
     author cannot see it: the confirmation prints the id that won.

Both fail toward RESUMING WORK TOO EARLY, which is the expensive direction.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from shantytown.deferrals import named_conditions, parse_condition, parse_conditions
from shantytown.dispatch import DeferRefused, Dispatcher
from shantytown.files import FilesRegistry, FilesTracker
from shantytown.tmux import NullPanes


@pytest.fixture
def world(tmp_path: Path):
    tracker = FilesTracker(tmp_path / "items")
    tracker.update("item-1", title="a blocked thing", status="open", assignee="kelly")
    return Dispatcher(FilesRegistry(tmp_path / "crew"), tracker, NullPanes()), tracker


# ── the write side ──────────────────────────────────────────────────────────

def test_a_reason_naming_TWO_blockers_is_REFUSED_not_silently_halved(world):
    d, _ = world
    with pytest.raises(DeferRefused) as e:
        d.defer("item-1", "bead",
                "resume_when: closed:aegis-mzdcm0 (and closed:aegis-nt4rap - BOTH required)")
    msg = str(e.value)
    assert "closed:aegis-mzdcm0" in msg and "closed:aegis-nt4rap" in msg, (
        "the refusal must name BOTH ids — the author's whole problem is that one "
        "of them vanished silently")
    assert "dep add" in msg, "it must point at the mechanism that DOES express conjunction"


def test_a_STALE_marker_in_the_notes_cannot_outrank_the_reason_being_written(world):
    """Defect 2: the specimen that actually fired on nrajcw."""
    d, tracker = world
    tracker.update("item-1", notes="an earlier deferral\nresume_when: closed:aegis-nt4rap\n")
    with pytest.raises(DeferRefused) as e:
        d.defer("item-1", "bead", "resume_when: closed:aegis-mzdcm0")
    msg = str(e.value)
    assert "closed:aegis-mzdcm0" in msg, "must name what the author wrote"
    assert "closed:aegis-nt4rap" in msg, "must name the stale marker that would have won"


def test_ONE_condition_still_defers_normally(world):
    """CONTROL. The refusals must not break the ordinary single-blocker defer."""
    d, tracker = world
    d.defer("item-1", "bead", "resume_when: closed:aegis-mzdcm0")
    assert parse_condition(tracker.get("item-1").notes or "").render() == "closed:aegis-mzdcm0"


def test_RE_deferring_on_the_SAME_condition_is_not_an_ambiguity(world):
    """CONTROL. A bead re-deferred on the condition it already carries is
    unchanged, not conflicting — dedup must keep that quiet."""
    d, tracker = world
    tracker.update("item-1", notes="resume_when: closed:aegis-nt4rap\n")
    d.defer("item-1", "bead", "resume_when: closed:aegis-nt4rap")
    assert tracker.get("item-1").status == "deferred"


def test_an_UNTIL_date_is_unaffected_by_a_stale_marker(world):
    """CONTROL. `--until` names the condition explicitly, so the notes marker is
    not consulted and must not be able to refuse the call."""
    d, tracker = world
    tracker.update("item-1", notes="resume_when: closed:aegis-nt4rap\n")
    d.defer("item-1", "parked", "waiting for the window", until="2026-09-20")
    assert tracker.get("item-1").defer_until == "2026-09-20"


# ── the parsing primitives ──────────────────────────────────────────────────

def test_named_conditions_sees_the_second_id_that_the_marker_regex_cannot():
    """`_CONDITION` demands a `resume_when:` prefix per match, so the specimen
    parses as ONE condition while naming two. That gap is the defect."""
    reason = "resume_when: closed:aegis-A (and closed:aegis-B - BOTH required)"
    assert [c.render() for c in parse_conditions(reason)] == ["closed:aegis-A"]
    assert [c.render() for c in named_conditions(reason)] == ["closed:aegis-A", "closed:aegis-B"]


def test_duplicates_are_one_condition_not_an_ambiguity():
    assert [c.render() for c in named_conditions(
        "closed:aegis-A and again closed:aegis-A")] == ["closed:aegis-A"]


# ── the read side: the beads already in this state ──────────────────────────

def _row(**kw):
    row = {"id": "aegis-x", "title": "t", "status": "deferred", "notes": ""}
    row.update(kw)
    return row


def test_the_sweeper_REPORTS_several_markers_instead_of_honouring_one_silently():
    """aegis-0sp38d's shape: an UNTESTABLE kind in front of a testable one, so
    the sweeper called the bead untestable while a condition it could decide sat
    in the same notes."""
    from datetime import datetime, timezone

    from shantytown.deferrals import evaluate

    rows = [_row(notes="resume_when: human:2026-09-23\nx\nresume_when: closed:aegis-u6mdxf")]
    out = evaluate(rows, datetime(2026, 9, 16, tzinfo=timezone.utc),
                   is_closed=lambda _i: False)
    assert len(out) == 1
    msg = out[0].untestable
    assert "2 resume conditions" in msg, msg
    assert "human:2026-09-23" in msg and "closed:aegis-u6mdxf" in msg, (
        "both must be named — the reader's job is to delete the one that no "
        "longer applies, and they cannot do that from a count")
    assert "dependency edges" in msg, "say what DOES express conjunction"
    assert out[0].met is False, "several conditions is CANNOT TELL, never met"


def test_a_SINGLE_marker_is_reported_exactly_as_before():
    """CONTROL. The new leg must not capture the ordinary one-condition bead."""
    from datetime import datetime, timezone

    from shantytown.deferrals import evaluate

    rows = [_row(notes="resume_when: closed:aegis-u6mdxf")]
    out = evaluate(rows, datetime(2026, 9, 16, tzinfo=timezone.utc),
                   is_closed=lambda _i: True)
    assert len(out) == 1
    assert out[0].met is True, "a met single condition must still report as met"
    assert "resume conditions present" not in out[0].untestable


def test_the_SAME_marker_written_twice_is_not_an_ambiguity():
    """CONTROL. Dedup is what keeps re-deferral on an unchanged condition quiet."""
    from datetime import datetime, timezone

    from shantytown.deferrals import evaluate

    rows = [_row(notes="resume_when: closed:aegis-u6mdxf\nlater\nresume_when: closed:aegis-u6mdxf")]
    out = evaluate(rows, datetime(2026, 9, 16, tzinfo=timezone.utc),
                   is_closed=lambda _i: True)
    assert len(out) == 1
    assert out[0].met is True
    assert "resume conditions present" not in out[0].untestable


def test_a_defer_until_does_NOT_hide_the_ambiguity():
    """REGRESSION for my own first cut, which gated this on `defer_until` being
    absent and so missed its worst specimen. aegis-0sp38d carries a defer_until
    of 2026-09-17 AND three distinct conditions including two open blockers: the
    date lapses first and the bead resurfaces while both stand."""
    from datetime import datetime, timezone

    from shantytown.deferrals import evaluate

    rows = [_row(defer_until="2026-09-17T13:00:00Z",
                 notes=("resume_when: human:2026-09-23\n"
                        "resume_when: closed:aegis-u6mdxf\n"
                        "resume_when: closed:aegis-5vnbv5\n"))]
    out = evaluate(rows, datetime(2026, 9, 18, tzinfo=timezone.utc),
                   is_closed=lambda _i: True)
    assert len(out) == 1
    assert "3 resume conditions" in out[0].untestable, out[0].untestable
    assert out[0].met is False, (
        "the date lapsed and the first marker is unreadable — reporting met "
        "here is the resume-too-early failure this bead is about")


def test_a_lapsed_date_with_ONE_condition_still_reports_met():
    """CONTROL for the test above: the ambiguity leg must not swallow the
    ordinary lapsed-and-met verdict."""
    from datetime import datetime, timezone

    from shantytown.deferrals import evaluate

    rows = [_row(defer_until="2026-09-17T13:00:00Z",
                 notes="resume_when: closed:aegis-u6mdxf")]
    out = evaluate(rows, datetime(2026, 9, 18, tzinfo=timezone.utc),
                   is_closed=lambda _i: True)
    assert len(out) == 1 and out[0].met is True
