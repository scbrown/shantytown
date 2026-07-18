"""The stop-event stream — persist(survival) + drain(delivery) + BLOCK-ONCE.
shantytown #6 (aegis-ct5q, arnold's ruling gt-wisp-w4j2af).

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
    store.persist(to="goldblum", frm="ellie", reason="lead-unreachable", rose=True)
    got = store.drain("goldblum")
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
    store.persist(to="goldblum", frm="grant", reason=None, rose=False)
    assert [e.frm for e in store.drain("maldoon")] == ["ellie"]
    assert [e.frm for e in store.drain("goldblum")] == ["grant"]


def test_empty_drain_is_not_an_error(store):
    """A destination with nothing pending drains [] — the idle case, the common
    case, and NOT a failure."""
    assert store.drain("nobody-home") == []
