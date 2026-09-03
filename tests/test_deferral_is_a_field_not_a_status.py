"""A deferred bead must never reach a plate — and on br, deferral is a FIELD.

aegis-vyc3aa. The clbx2 cutover (2026-08-29) moved the crew store from Dolt to br
and moved deferral from `status = 'deferred'` to a `defer_until` TIMESTAMP with
`status` left as `open`. `inbox.is_deferred` tested the status, so it went blind
at the cutover WHILE CONTINUING TO ANSWER, and both plate readers resumed serving
deferred beads.

MEASURED 2026-09-02 on the live store:

    br show aegis-f46wu                 -> status 'open',
                                           defer_until '2026-09-03T13:00:00Z'
    br ready --limit 0 | grep -c f46wu  -> 0    <- ready EXCLUDES it, correctly
    st anchor dearing                   -> "ON YOUR PLATE: aegis-f46wu"

A coordinator had deferred that bead specifically so the queue would feed past
it. The haul did; the anchor did not.

WHY THIS ONE IS WORTH THE PROSE: the existing design was RIGHT and did not save
us. `is_unworkable` was deliberately composed in one place so "a new status is
ONE edit and both backends move together by construction" — and that seam was
intact and irrelevant, because what changed was not a new status but the
REPRESENTATION of an old one. A migration kills READERS, not writers: `br ready`
is writer-side and was updated; this reader was not.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from shantytown import inbox


def _iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat().replace("+00:00", "Z")


# --- the predicate ----------------------------------------------------------

def test_a_future_defer_until_is_deferred_even_when_status_is_open():
    """The regression, in the shape br actually stores.

    The stamp is RELATIVE, not the measured literal. It was
    `2026-09-03T13:00:00Z` — copied from the live row above, which reads as
    faithful — and at 13:00Z on 2026-09-03 the future became the past and this
    test began failing for everyone, permanently. A test asserting "deferred"
    may not hold a fixed date: the property is that the stamp is AHEAD OF NOW,
    and the only honest way to write that is against the clock the code reads.
    The measured literal is preserved in the module docstring, where it is
    provenance rather than an assertion.
    """
    row = {"id": "aegis-f46wu", "status": "open",
           "defer_until": _iso(timedelta(days=1))}
    assert inbox.is_deferred(row) is True
    assert inbox.is_unworkable(row) is True


def test_a_LAPSED_defer_until_is_workable_again():
    """The whole point of a deadline. Without this the fix would withhold a bead
    forever once deferred."""
    row = {"id": "aegis-1", "status": "open", "defer_until": _iso(timedelta(days=-1))}
    assert inbox.is_deferred(row) is False
    assert inbox.is_unworkable(row) is False


def test_an_ordinary_open_row_is_still_workable():
    """The control. A predicate that started returning True for everything would
    pass the first test and silently empty every plate on the fleet."""
    assert inbox.is_unworkable({"id": "aegis-2", "status": "open"}) is False


def test_an_unparseable_or_absent_stamp_fails_toward_WORKABLE():
    """Deliberate direction. Misreading a stamp costs one turn on a bead that
    was going to be workable anyway; the other direction silently withholds an
    agent from its own queue with no signal anywhere."""
    for bad in ("", None, "soon", "not-a-date", 0):
        assert inbox.is_unworkable({"status": "open", "defer_until": bad}) is False, bad


def test_the_old_STATUS_form_still_works():
    """Dolt is retained for rollback, so the status form must not regress."""
    assert inbox.is_deferred("deferred") is True
    assert inbox.is_unworkable({"status": "deferred"}) is True


def test_a_naive_stamp_is_read_as_UTC_not_crashed_on():
    """br writes Z-suffixed UTC, but a hand-edited or older row may be naive.
    Comparing naive to aware raises TypeError, which the caller would see as a
    crashed plate read — the fleet-wide outage plate() exists to refuse."""
    naive = (datetime.now(timezone.utc) + timedelta(days=1)
             ).replace(tzinfo=None).isoformat()
    assert inbox.is_unworkable({"status": "open", "defer_until": naive}) is True


# --- both plate readers, so the two backends cannot drift again -------------

def test_both_plate_readers_refuse_a_field_deferred_row(monkeypatch):
    """The two-implementation rule, applied to the thing that actually moved.
    Checked through is_unworkable on the ROW — which is what each reader now
    passes — so a reader that reverts to `row.get("status")` fails here."""
    from shantytown import beads, br
    import inspect
    for mod, name in ((beads, "beads.plate"), (br, "br.plate")):
        src = inspect.getsource(mod.plate)
        assert "is_unworkable(x)" in src or "is_unworkable(row)" in src, (
            f"{name} must pass the ROW to is_unworkable, not just the status — "
            f"deferral is a FIELD on br (aegis-vyc3aa)")
