"""The stop-event stream — persist(survival) + drain(delivery) + BLOCK-ONCE.
shantytown #6 (arnold's ruling gt-wisp-w4j2af).

The two rails under test: (1) survival — an event persists even when the
destination is down, and drains when the destination next stops; (2) BLOCK-ONCE —
a destination with one pending event drains it ONCE, then a later drain is empty
so it can idle. A drain that re-returns delivered events wedges the tier.
"""
from __future__ import annotations

import pytest

from shantytown.events import FilesEvents, NullEvents, StopEvent


@pytest.fixture(params=["files", "null"])
def store(request, tmp_path):
    """Both implementations, same tests — the two-impl rule as a fixture. If one
    behaves differently, the store leaked its backend into its semantics."""
    return FilesEvents(tmp_path / "events") if request.param == "files" else NullEvents()


# --- survival: persisted before read, does not need the destination live --------

def test_persist_survives_a_down_destination(store):
    ev = store.persist(to="maldoon", frm="ellie", reason=None, rose=False)
    assert ev.to == "maldoon" and ev.frm == "ellie"
    # maldoon never had to be "up" — the event is already recorded and drainable.
    drained = store.drain("maldoon")
    assert [e.frm for e in drained] == ["ellie"]


def test_a_rise_carries_its_reason(store):
    store.persist(to="hammond", frm="ellie", reason="lead-unreachable", rose=True)
    got = store.drain("hammond")
    assert len(got) == 1
    assert got[0].rose is True and got[0].reason == "lead-unreachable"


# --- BLOCK-ONCE: the rail that keeps a lead from wedging -------------------------

def test_drain_returns_each_event_ONCE_then_idles(store):
    """A destination with one pending event drains it once; the NEXT drain is
    empty — so a later stop lets it idle instead of re-blocking forever."""
    store.persist(to="maldoon", frm="ellie", reason=None, rose=False)
    first = store.drain("maldoon")
    assert len(first) == 1, "first drain must deliver the event"
    second = store.drain("maldoon")
    assert second == [], "BLOCK-ONCE violated: a delivered event drained again -> wedge"


def test_a_new_event_after_a_drain_is_delivered(store):
    """Block-once marks the delivered ones, not the stream — a fresh event still
    drains. (Otherwise the lead would go deaf after its first event.)"""
    store.persist(to="maldoon", frm="ellie", reason=None, rose=False)
    store.drain("maldoon")                       # delivers #1
    store.persist(to="maldoon", frm="tim", reason=None, rose=False)   # #2 arrives
    got = store.drain("maldoon")
    assert [e.frm for e in got] == ["tim"], "a new event after a drain must deliver"


# --- addressing: I only drain what is addressed to ME ---------------------------

def test_drain_is_scoped_to_the_recipient(store):
    store.persist(to="maldoon", frm="ellie", reason=None, rose=False)
    store.persist(to="hammond", frm="grant", reason=None, rose=False)
    assert [e.frm for e in store.drain("maldoon")] == ["ellie"]
    assert [e.frm for e in store.drain("hammond")] == ["grant"]


# --- accept: DEFERRAL, not filtering (internal-ref) --------------------------------

def test_a_rejected_event_stays_pending_and_delivers_later(store):
    """The reader declines to be woken by a turn boundary. If `accept` DROPPED the
    event instead of holding it, a stop would be lost every time its sender
    happened to still be mid-flight — a silent hole exactly where the tier is
    supposed to be durable."""
    store.persist(to="maldoon", frm="ellie", reason=None, rose=False)
    assert store.drain("maldoon", lambda e: False) == [], "rejected -> not delivered"
    assert [e.frm for e in store.drain("maldoon")] == ["ellie"], \
        "a rejected event was consumed, not deferred — the stop is gone"


def test_accept_is_per_event(store):
    store.persist(to="maldoon", frm="ellie", reason=None, rose=False)
    store.persist(to="maldoon", frm="tim", reason=None, rose=False)
    got = store.drain("maldoon", lambda e: e.frm == "tim")
    assert [e.frm for e in got] == ["tim"]
    assert [e.frm for e in store.drain("maldoon")] == ["ellie"]


# --- the fields that make an event actionable ------------------------------------

def test_persist_stamps_a_time_and_records_the_item(store):
    ev = store.persist(to="maldoon", frm="ellie", reason=None, rose=False,
                       item="it-7", item_status="in_progress")
    assert ev.ts > 0, "no timestamp -> events cannot be ordered, aged, or trusted"
    got = store.drain("maldoon")[0]
    assert (got.item, got.item_status, got.ts) == ("it-7", "in_progress", ev.ts)


def test_drain_returns_events_oldest_first(store):
    """ev-10 sorts BEFORE ev-2 as a string, and the reader now picks the LATEST
    event per agent — so a lexicographic order would hand it the wrong one."""
    for i in range(11):
        store.persist(to="maldoon", frm=f"a{i}", reason=None, rose=False)
    got = store.drain("maldoon")
    assert [e.frm for e in got] == [f"a{i}" for i in range(11)]


def test_empty_drain_is_not_an_error(store):
    """A destination with nothing pending drains [] — the idle case, the common
    case, and NOT a failure."""
    assert store.drain("nobody-home") == []


# --- corruption must not dam the store (the ev-172 incident, 2026-07-23) ---------

def test_one_corrupt_file_does_not_dam_the_store(tmp_path, capsys):
    """An EMPTY event file (writer killed mid-write, pre-atomic persist) made
    pending() raise for EVERY destination: 47 events sat undeliverable behind one
    0-byte file, the coordinator's Stop hook died on every stop, and a closed
    security bead was re-slung twice from the resulting stale picture (internal-ref).
    The store must deliver everything it CAN read, and say what it skipped."""
    store = FilesEvents(tmp_path / "events")
    store.persist(to="maldoon", frm="ellie", reason=None, rose=False)
    (tmp_path / "events" / "ev-172.json").write_text("")          # the specimen
    store.persist(to="maldoon", frm="tim", reason=None, rose=False)
    got = store.drain("maldoon")
    assert [e.frm for e in got] == ["ellie", "tim"], \
        "readable events on both sides of the corrupt file must still deliver"
    assert "ev-172" in capsys.readouterr().err, \
        "the skip must be LOUD — silent tolerance hides the corruption forever"


def test_persist_is_atomic_no_tmp_left_behind(tmp_path):
    """persist() goes through tmp+rename now; the tmp must not survive, or the
    glob would try to read it (it matches nothing today, but a leaked partial
    file is exactly the class that caused this incident)."""
    store = FilesEvents(tmp_path / "events")
    store.persist(to="maldoon", frm="ellie", reason=None, rose=False)
    left = list((tmp_path / "events").glob("*.tmp"))
    assert left == [], f"tmp files leaked: {left}"
