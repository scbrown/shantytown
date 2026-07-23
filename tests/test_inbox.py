"""The inbox — a THIRD type on the substrate, kept OFF the plate (inbox.py).

Two implementations, one set of tests, same as test_events.py: FilesInbox (its own
directory under the .shanty root) and TrackerInbox (a ticket item on the selected
tracker). If one behaves differently, the concept leaked its backend.

The properties that matter, in order of what they cost when wrong:

  1. unread() MARKS NOTHING. `st inbox --count` polls it. A read that consumed
     what it reported would delete the message before the recipient ever saw it —
     the same class of bug as a stop-event counter that drained (events.py's RAIL),
     and we are not shipping it twice.
  2. A MESSAGE IS NOT WORK. The plate holds at most ONE item, so a message that
     reached it EVICTS the agent's real work. This was not hypothetical: `st mail
     -d` created plain tracker items assigned to the recipient, and they are on
     the live aegis store today.
  3. mark_read is the ACK, and it is idempotent-ish: acked messages do not come
     back, and a NEW message after an ack still arrives (the same block-once shape
     the events store needs, for the same reason).
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown.files import FilesTracker, items as files_items, plate as files_plate
from shantytown.inbox import FilesInbox, Inbox, MessageTooLong, TrackerInbox, is_message
from shantytown.protocols import WorkItem


@pytest.fixture(params=["files", "tracker"])
def box(request, tmp_path):
    if request.param == "files":
        return FilesInbox(tmp_path / "inbox")
    trk = FilesTracker(tmp_path / "items")
    return TrackerInbox(trk, lambda: files_items(trk))


# --- the protocol -----------------------------------------------------------

def test_both_implementations_satisfy_the_protocol(box):
    assert isinstance(box, Inbox)


# --- delivery: it survives a recipient who is not there ---------------------

def test_a_message_survives_a_recipient_who_never_looked(box):
    box.deliver("maldoon", "HANDOFF: finish qdal.2")
    got = box.unread("maldoon")
    assert [m.body for m in got] == ["HANDOFF: finish qdal.2"]


def test_only_my_messages(box):
    box.deliver("maldoon", "yours")
    box.deliver("ellie", "hers")
    assert [m.body for m in box.unread("maldoon")] == ["yours"]
    assert [m.body for m in box.unread("ellie")] == ["hers"]


def test_an_empty_inbox_is_empty_not_an_error(box):
    assert box.unread("nobody-wrote-to-me") == []


# --- 1. counting must not consume -------------------------------------------

def test_unread_marks_nothing(box):
    """Read it three times, get the same answer three times, and it is STILL
    there for the ack. `st inbox --count` runs on a timer."""
    box.deliver("maldoon", "one")
    box.deliver("maldoon", "two")
    assert len(box.unread("maldoon")) == 2
    assert len(box.unread("maldoon")) == 2
    assert len(box.unread("maldoon")) == 2
    assert len(box.mark_read("maldoon")) == 2, "unread() consumed what it counted"


# --- 3. the ack -------------------------------------------------------------

def test_acked_messages_do_not_come_back(box):
    box.deliver("maldoon", "one")
    box.mark_read("maldoon")
    assert box.unread("maldoon") == []


def test_a_new_message_after_an_ack_still_arrives(box):
    """Otherwise the recipient goes deaf after their first message — the same
    failure block-once has to avoid one type over."""
    box.deliver("maldoon", "one")
    box.mark_read("maldoon")
    box.deliver("maldoon", "two")
    assert [m.body for m in box.unread("maldoon")] == ["two"]


def test_ack_can_name_the_ones_it_acks(box):
    box.deliver("maldoon", "one")
    box.deliver("maldoon", "two")
    first = box.unread("maldoon")[0]
    marked = box.mark_read("maldoon", ids=[first.id])
    assert [m.id for m in marked] == [first.id]
    assert [m.body for m in box.unread("maldoon")] == ["two"]


# --- 2. a message is not work ----------------------------------------------

def test_a_tracker_backed_message_never_reaches_the_plate(tmp_path: Path):
    """THE ONE THAT COST SOMETHING. TrackerInbox writes to the SAME store the
    plate reads, so the exclusion is a marker, and this is the test that the
    marker works. Without it, `st inbox ellie "nice work"` becomes ellie's plate
    and her actual P1 disappears behind it."""
    trk = FilesTracker(tmp_path / "items")
    box = TrackerInbox(trk, lambda: files_items(trk))
    trk.update("internal-ref", title="Restore the den service",
               status="in_progress", assignee="ellie")
    box.deliver("ellie", "nice work")

    on_plate = files_plate(trk, "ellie")
    assert on_plate is not None and on_plate.id == "internal-ref", (
        "a message evicted the agent's work from the plate")
    # ...and it is genuinely there, in the inbox, not simply dropped.
    assert [m.body for m in box.unread("ellie")] == ["nice work"]


def test_a_files_inbox_is_structurally_off_the_plate(tmp_path: Path):
    """The other implementation does not NEED a marker: it is a different
    directory, which no plate reader globs. Same guarantee, stronger mechanism —
    that asymmetry is why there are two implementations."""
    trk = FilesTracker(tmp_path / "items")
    FilesInbox(tmp_path / "inbox").deliver("ellie", "nice work")
    assert files_plate(trk, "ellie") is None
    assert not list((tmp_path / "items").glob("*.json")) if (tmp_path / "items").is_dir() else True


def test_the_legacy_mail_prefix_is_excluded_too(tmp_path: Path):
    """`st mail -d` items titled "mail: ..." are open and assigned on the live
    store RIGHT NOW, i.e. sitting on real plates. Excluding the old prefix is not
    tidiness — it un-breaks the plates that are already broken."""
    trk = FilesTracker(tmp_path / "items")
    trk.update("internal-ref", title="mail: HANDOFF from before the inbox",
               status="open", assignee="ellie")
    assert is_message("mail: anything")
    assert files_plate(trk, "ellie") is None


def test_work_that_merely_mentions_a_message_is_still_work():
    """The predicate is a PREFIX, not a substring. "fix the inbox: it drops
    messages" is work, and a plate that hid it would be worse than the bug."""
    assert not is_message("fix the inbox: it drops messages")
    assert not is_message("audit mail: routing")
    assert is_message("inbox: hello")
    assert is_message("  inbox: leading space still counts")


# --- the tracker mapping, stated ------------------------------------------

def test_the_tracker_mapping_is_an_ordinary_item_a_human_can_see(tmp_path: Path):
    """Not an opaque blob: a message on the tracker is a normal item, assigned to
    the recipient, labelled `inbox`, that anyone can list with the tracker's own
    tools. That is the whole point of mapping it onto the ticket system."""
    trk = FilesTracker(tmp_path / "items")
    msg = TrackerInbox(trk, lambda: files_items(trk)).deliver(
        "ellie", "read st-1", frm="sattler")
    raw = json.loads((tmp_path / "items" / f"{msg.id}.json").read_text())
    assert raw["title"] == "inbox: read st-1"
    assert raw["assignee"] == "ellie"
    assert raw["labels"] == "inbox"
    assert "sattler" in raw["description"]


def test_a_tracker_message_reports_an_unknown_sender_rather_than_guessing(tmp_path: Path):
    """A WorkItem carries id/title/status/assignee — there is nowhere honest to
    read `frm` back from, so unread() says None. None means "we do not know",
    which is a different claim from naming somebody."""
    trk = FilesTracker(tmp_path / "items")
    box = TrackerInbox(trk, lambda: files_items(trk))
    box.deliver("ellie", "hi", frm="sattler")
    assert box.unread("ellie")[0].frm is None


def test_the_files_inbox_does_keep_the_sender(tmp_path: Path):
    """...and the implementation that CAN carry it, does. The asymmetry is real
    and is reported, not smoothed over."""
    box = FilesInbox(tmp_path / "inbox")
    box.deliver("ellie", "hi", frm="sattler")
    assert box.unread("ellie")[0].frm == "sattler"


# --- the durable channel is thin, and refuses cleanly when a message won't fit
# (internal-ref). The cap is the TRACKER's (bd = 500), not the inbox's — so only a
# backend that declares one refuses; the files store carries any length.

class _CappedTracker:
    """A tracker with bd's 500-char title cap, and nothing else."""
    _TITLE_MAX = 500

    def __init__(self):
        self.created = []

    def create(self, title, **fields):
        self.created.append(title)
        return WorkItem(id="st-c1", title=title, status="open",
                        assignee=fields.get("assignee"))


def test_tracker_inbox_refuses_a_message_over_the_title_cap(tmp_path: Path):
    """A body that would push the `inbox: <body>` title past the tracker's cap is
    refused with MessageTooLong BEFORE any write — never a leaked bd error, never a
    silent truncation. The message names the remedy (a bead + a pointer)."""
    tracker = _CappedTracker()
    box = TrackerInbox(tracker, lambda: [])
    with pytest.raises(MessageTooLong) as ei:
        box.deliver("ellie", "x" * 494)          # "inbox: " + 494 = 501 > 500
    assert "bead" in str(ei.value)
    assert tracker.created == [], "a refused message must not be written"


def test_tracker_inbox_delivers_at_the_cap_boundary(tmp_path: Path):
    tracker = _CappedTracker()
    box = TrackerInbox(tracker, lambda: [])
    box.deliver("ellie", "x" * 493)              # title == 500, exactly fits
    assert len(tracker.created) == 1


def test_a_backend_with_no_title_cap_carries_a_long_message(tmp_path: Path):
    """The cap is the tracker's, not the inbox's: FilesInbox (no title, no cap) and
    a TrackerInbox over an uncapped tracker both carry a 2000-char body. The refusal
    is not a blanket inbox limit — it is honouring the concrete store's real one."""
    long = "y" * 2000
    fbox = FilesInbox(tmp_path / "inbox")
    fbox.deliver("ellie", long)
    assert fbox.unread("ellie")[0].body == long

    trk = FilesTracker(tmp_path / "items")        # FilesTracker declares no TITLE_MAX
    tbox = TrackerInbox(trk, lambda: files_items(trk))
    tbox.deliver("maldoon", long)                 # must not raise
    assert tbox.unread("maldoon")[0].body == long
