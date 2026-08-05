"""A MESSAGE IS NOT WORK — and the haul must agree with the plate (aegis-atc0).

inbox.py's whole argument is that a message is a third type which must never
occupy an agent's plate, and BOTH plate readers enforce it (files.plate,
beads.plate, one shared is_message predicate so the two backends cannot drift).
The haul advance and the Rule Zero feed gate did not, so they handed the agent
exactly the items the plate had refused.

MEASURED 2026-07-24: the haul fed weaver aegis-atc0, titled `inbox: tim: ...`,
with "read it and execute; close it when done". The work it described was
billy's and already closed. There was nothing to execute.
"""
from __future__ import annotations

from shantytown.feed_check import hauls
from shantytown.stop_event import _assigned_to


def _bead(bid, title, assignee="beads_aegis/crew/weaver", status="open"):
    return {"id": bid, "title": title, "assignee": assignee, "status": status}


# --- the haul advance -------------------------------------------------------

def test_haul_ready_set_excludes_inbox_messages():
    got = _assigned_to("weaver", [
        _bead("st-1", "inbox: tim: PR #130 is ready"),
        _bead("st-2", "mail: legacy spelling, still on the live store"),
        _bead("st-3", "Wire the family ratings page"),
    ])
    assert [b["id"] for b in got] == ["st-3"], (
        "the haul would feed a message as work — the plate already refuses these")


def test_haul_still_feeds_real_work():
    """The filter must not eat the feature. A haul that returns nothing is not
    a fixed haul."""
    got = _assigned_to("weaver", [_bead("st-9", "Fix the COALESCE scan bug")])
    assert [b["id"] for b in got] == ["st-9"]


def test_haul_matches_the_plate_reader_exactly():
    """THE POINT OF THE FIX: one predicate, so haul and plate cannot disagree
    about what an agent's work is. Asserted against inbox.is_message itself
    rather than a copied list of prefixes."""
    from shantytown.inbox import is_message
    titles = ["inbox: hi", "mail: hi", "  inbox: leading space", "real work",
              "inboxes are not messages"]
    kept = {b["title"] for b in _assigned_to("weaver", [_bead(f"st-{i}", t)
                                                        for i, t in enumerate(titles)])}
    assert kept == {t for t in titles if not is_message(t)}


def test_an_unread_message_is_not_an_active_anchor():
    """_assigned_to also answers 'am I mid-work?' for the in_progress check. A
    message counted there would silently SUPPRESS the advance for a worker that
    is genuinely free — the opposite failure, same root."""
    assert _assigned_to("weaver", [
        _bead("st-1", "inbox: tim: fyi", status="in_progress")]) == []


# --- the Rule Zero feed gate ------------------------------------------------

def test_a_worker_holding_only_messages_is_not_self_feeding():
    """WORSE THAN THE HAUL BUG. hauls() decides who is SELF-FEEDING, and a
    self-feeding worker is EXCLUDED from the feedable free list. So an idle
    worker holding three unread messages read as "its next work is already
    determined": it vanished from the coordinator's free list and was dispatched
    nothing, while the gate that exists to stop the fleet going idle counted it
    as busy."""
    got = hauls([
        _bead("st-1", "inbox: a"), _bead("st-2", "inbox: b"), _bead("st-3", "mail: c"),
    ])
    assert got == {}, f"weaver looks self-feeding on messages alone: {got}"


def test_a_worker_with_real_ready_work_is_still_self_feeding():
    got = hauls([_bead("st-1", "inbox: noise"), _bead("st-2", "Real queued work")])
    assert got == {"weaver": ["st-2"]}


# ── in_progress belongs IN the haul (aegis-ap4gm) ────────────────────────────

def test_an_in_progress_item_enters_the_haul_and_ranks_FIRST():
    """The defect: `bd ready` means "unblocked and OPEN", so an item the worker
    ALREADY STARTED never appeared in any haul. The worker was in no queue, on no
    plate the feeder reads, and out of `bd ready` — it idled permanently and every
    instrument agreed it was fine. Four agents found in one sweep.

    It ranks FIRST because an item someone already started is the strongest
    possible next-item signal — stronger than anything merely ready."""
    from shantytown.feed_check import hauls

    ready = [{"id": "b-2", "assignee": "beads_aegis/crew/ellie", "title": "later"}]
    active = [{"id": "b-1", "assignee": "beads_aegis/crew/ellie", "title": "started"}]

    assert hauls(ready, active) == {"ellie": ["b-1", "b-2"]}


def test_hauls_without_the_second_argument_is_UNCHANGED():
    """Every existing caller and test passes one argument. The widening must be
    additive — if this ever differs, the change stopped being a superset and
    started being a behaviour swap."""
    from shantytown.feed_check import hauls

    ready = [{"id": "b-1", "assignee": "ellie", "title": "t"}]
    assert hauls(ready) == {"ellie": ["b-1"]}


def test_an_UNASSIGNED_in_progress_bead_is_NOT_papered_over():
    """The sharper half of the bead, and it must NOT be 'fixed' here. Re-pooling
    (`bd update -a ""`) clears the assignee and leaves the status at in_progress,
    so the bead leaves the board entirely — that is a real defect wanting a status
    reset, not something to hide by inventing an owner. If this ever returns a
    name, the orphan has been made invisible instead of visible."""
    from shantytown.feed_check import hauls

    orphan = [{"id": "b-9", "assignee": "", "title": "re-pooled, status left"}]
    assert hauls([], orphan) == {}


def test_an_in_progress_MESSAGE_is_still_not_a_queue():
    """The exclusions that already applied to ready beads must apply to the new
    source too, or the widening reintroduces the bug the message filter fixed: a
    worker holding only `inbox:` items reading as self-feeding and vanishing from
    the coordinator's free list."""
    from shantytown.feed_check import hauls

    active = [{"id": "m-1", "assignee": "ellie", "title": "inbox: [from x] hi"}]
    assert hauls([], active) == {}


def test_the_same_bead_in_both_sources_is_not_doubled():
    """Defensive: the two sources are disjoint by bd's status semantics today. A
    doubled queue entry would make a worker look like it has two items when it
    has one."""
    from shantytown.feed_check import hauls

    b = {"id": "b-1", "assignee": "ellie", "title": "t"}
    assert hauls([b], [b]) == {"ellie": ["b-1"]}
