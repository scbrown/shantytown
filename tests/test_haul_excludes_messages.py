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
