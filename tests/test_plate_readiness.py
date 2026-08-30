"""plate ordering is readiness-aware, and says so when it cannot be (aegis-fxx3y).

THE BUG. `plate()` sorted by (status_rank, ID) and never consulted readiness, so
among equal-status items the winner was decided ALPHABETICALLY. A correctly wired
six-child dep chain with exactly one ready head served child 4 — `aegis-8fydl`
beat `aegis-rl1kg` on spelling while being blocked BY it. The plate is read as an
instruction ("execute immediately"), so that is not a display nit: anchor ordered
an agent to execute work that could not proceed and hid the one item that could.

THE TRAP THIS FILE EXISTS TO PIN. The obvious fix — "prefer items in `ready`" —
is wrong in a way that looks right. The ready listing contains ONLY `open` rows
(measured: 39 of 39 on the live store); `in_progress` and `hooked` are absent by
construction, not because anything blocks them. Ordering naively on membership
therefore demotes every item an agent is actually holding beneath any not-started
one. That is a worse bug than the original, and it would read as a deliberate
re-prioritisation rather than an artefact. `test_in_hand_*` are the guards.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from shantytown.beads import BeadsTracker, plate
from shantytown.protocols import WorkItem, is_blocked, plate_key


class FakeStore(BeadsTracker):
    """A tracker whose `list` and `ready` answer DIFFERENTLY — the whole point.

    A fake that returns the same rows for every subcommand cannot see this bug at
    all: every listed item would look ready, so a blocked item and a ready one
    would be indistinguishable and any ordering would pass.
    """

    def __init__(self, rows, ready_ids=(), ready_rc=0, deps=None):
        super().__init__()
        self._rows = rows
        self._ready = [r for r in rows if r.get("id") in set(ready_ids)]
        self._ready_rc = ready_rc
        self._deps = deps or {}

    def _bd(self, *args):
        if args and args[0] == "ready":
            if self._ready_rc != 0:
                return SimpleNamespace(returncode=self._ready_rc, stdout="",
                                       stderr="ready exploded")
            return SimpleNamespace(returncode=0, stdout=json.dumps(self._ready),
                                   stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps(self._rows),
                               stderr="")

    def get(self, item_id):
        return WorkItem(id=item_id, open_blockers=tuple(self._deps.get(item_id, ())))


def row(ident, status="open", priority=None, assignee="ada", title="t"):
    d = {"id": ident, "status": status, "assignee": assignee, "title": title}
    if priority is not None:
        d["priority"] = priority
    return d


# --------------------------------------------------------------------------- #
# The reported bug, end to end
# --------------------------------------------------------------------------- #

def test_the_ready_head_wins_over_an_alphabetically_earlier_blocked_item():
    """The exact aegis-fxx3y shape: 8fydl sorts first but is blocked by rl1kg."""
    t = FakeStore([row("aegis-8fydl"), row("aegis-rl1kg")],
                  ready_ids=["aegis-rl1kg"])
    assert plate(t, "ada").id == "aegis-rl1kg"


def test_and_the_old_ordering_would_have_got_it_wrong():
    """Control: without readiness, the alphabetical loser is what you get.

    Without this the test above proves nothing — 'rl1kg' could be winning for
    some unrelated reason and the assertion would still pass.
    """
    ids = sorted(["aegis-8fydl", "aegis-rl1kg"])
    assert ids[0] == "aegis-8fydl"


def test_all_blocked_still_serves_an_item_rather_than_an_empty_plate():
    t = FakeStore([row("b-2"), row("b-1")], ready_ids=[])
    assert plate(t, "ada").id == "b-1"


def test_an_all_blocked_plate_names_its_blocker():
    t = FakeStore([row("b-1")], ready_ids=[], deps={"b-1": ("up-9",)})
    assert plate(t, "ada").open_blockers == ("up-9",)


def test_an_unblocked_plate_pays_no_blocker_lookup():
    t = FakeStore([row("b-1")], ready_ids=["b-1"], deps={"b-1": ("up-9",)})
    # Not blocked, so the annotation is never fetched even though deps exist.
    assert plate(t, "ada").open_blockers == ()


# --------------------------------------------------------------------------- #
# The regression the naive fix would have introduced
# --------------------------------------------------------------------------- #

def test_in_hand_work_is_never_marked_blocked():
    # `ready` lists only open rows, so in_progress is absent from it ALWAYS.
    assert is_blocked("in_progress", "x", set()) is False
    assert is_blocked("hooked", "x", set()) is False


def test_in_hand_work_outranks_a_ready_not_started_item():
    t = FakeStore([row("a-in", status="in_progress"), row("z-open")],
                  ready_ids=["z-open"])
    assert plate(t, "ada").id == "a-in"


def test_open_is_blocked_only_when_absent_from_a_readiness_answer():
    assert is_blocked("open", "x", {"x"}) is False
    assert is_blocked("open", "x", {"y"}) is True


# --------------------------------------------------------------------------- #
# Could-not-look is not blocked
# --------------------------------------------------------------------------- #

def test_unknown_readiness_never_invents_a_blocker():
    assert is_blocked("open", "x", None) is False


def test_a_failed_readiness_call_degrades_to_the_old_ordering():
    """rc!=0 must mean 'could not tell', never 'nothing is ready'.

    An empty set would mark every item blocked and reorder the whole plate on the
    strength of a call that did not work.
    """
    t = FakeStore([row("a-1"), row("b-2")], ready_ids=[], ready_rc=1)
    assert plate(t, "ada").id == "a-1"


# --------------------------------------------------------------------------- #
# Priority before id — the smaller fix, worth having on its own
# --------------------------------------------------------------------------- #

def test_priority_beats_alphabetical_id():
    t = FakeStore([row("a-low", priority=3), row("z-high", priority=0)],
                  ready_ids=["a-low", "z-high"])
    assert plate(t, "ada").id == "z-high"


def test_unstated_priority_sorts_after_a_stated_one():
    # `_priority` refuses to invent a 2; ordering must not re-invent it either.
    stated = plate_key("open", 3, "a", {"a", "b"})
    unstated = plate_key("open", None, "b", {"a", "b"})
    assert stated < unstated


def test_id_is_still_the_final_deterministic_tiebreak():
    t = FakeStore([row("b-2", priority=1), row("a-1", priority=1)],
                  ready_ids=["a-1", "b-2"])
    assert plate(t, "ada").id == "a-1"


# --------------------------------------------------------------------------- #
# Key precedence, stated directly
# --------------------------------------------------------------------------- #

def test_key_order_is_rank_then_blocked_then_priority_then_id():
    ready = {"r"}
    assert (plate_key("in_progress", 9, "z", ready)
            < plate_key("open", 0, "r", ready))          # rank beats all
    assert (plate_key("open", 9, "r", ready)
            < plate_key("open", 0, "b", ready))          # unblocked beats priority
    assert (plate_key("open", 0, "r", ready)
            < plate_key("open", 5, "r", ready))          # then priority


# --------------------------------------------------------------------------- #
# The display half — a blocked instruction must SAY it is blocked
# --------------------------------------------------------------------------- #

def _anchoring(item):
    from shantytown.anchor import Anchoring
    from shantytown.protocols import Agent
    me = Agent(name="ada", role="worker")
    return Anchoring(me=me, item=item, lead=None, lead_up=None,
                     context=[], knowledge=[], admin="ada")


def test_a_blocked_plate_renders_the_blocker_and_what_to_do():
    out = _anchoring(WorkItem(id="b-1", title="T", open_blockers=("up-9",))).render()
    assert "BLOCKED by up-9" in out
    assert "Chase the blocker" in out


def test_an_unblocked_plate_renders_no_blocked_line():
    out = _anchoring(WorkItem(id="b-1", title="T")).render()
    assert "BLOCKED" not in out
