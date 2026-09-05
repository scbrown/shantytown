"""Both-outcome tests for the plate publish/read contract (aegis-qdjof).

Every UNKNOWN case gets a paired positive: a test that only proves "returns
None on bad input" cannot distinguish a working reader from one that returns
None unconditionally. Each staleness guard is therefore tested twice — once
where it must fire and once where it must NOT — which is the only shape that
catches a guard wired inside-out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from shantytown.plate_publish import own_session, plate_path, publish, publish_id, read


@dataclass
class _Item:
    id: str


def test_publish_then_read_round_trips(tmp_path):
    assert publish(tmp_path, "grant", _Item("aegis-368cu.9")) is True
    assert read(tmp_path, "grant") == "aegis-368cu.9"


def test_payload_shape_is_the_documented_contract(tmp_path):
    """Consumers are in another language and cannot import this module; the
    on-disk shape IS the interface, so it is asserted literally."""
    publish(tmp_path, "grant", _Item("aegis-1"), session="sess-a", _now=1234.0)
    data = json.loads(plate_path(tmp_path, "grant").read_text())
    assert data == {"item": "aegis-1", "at": 1234, "session": "sess-a"}


def test_plate_lives_outside_the_card_glob(tmp_path):
    """crew/*.json must not pick up a plate — the card and the plate are
    different kinds of thing with different lifetimes."""
    publish(tmp_path, "grant", _Item("aegis-1"))
    assert list((tmp_path / "crew").glob("*.json")) == []
    assert plate_path(tmp_path, "grant").exists()


# --- empty plate is a FACT, not a skipped write ----------------------------

def test_empty_plate_clears_a_previous_item(tmp_path):
    """The attributed-to-a-closed-bead failure, prevented at the source: after
    a bead closes the plate resolves to None and that must OVERWRITE, not be
    skipped."""
    publish(tmp_path, "grant", _Item("aegis-old"))
    assert read(tmp_path, "grant") == "aegis-old"
    publish(tmp_path, "grant", None)
    assert read(tmp_path, "grant") is None


# --- UNKNOWN cases, each with its positive twin ----------------------------

def test_missing_file_is_unknown(tmp_path):
    assert read(tmp_path, "nobody") is None


def test_malformed_json_is_unknown_but_valid_json_is_not(tmp_path):
    p = plate_path(tmp_path, "grant")
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    assert read(tmp_path, "grant") is None
    p.write_text(json.dumps({"item": "aegis-2", "at": 1, "session": None}))
    assert read(tmp_path, "grant") == "aegis-2"


def test_session_mismatch_is_unknown_and_match_is_not(tmp_path):
    publish(tmp_path, "grant", _Item("aegis-3"), session="sess-a")
    assert read(tmp_path, "grant", session="sess-b") is None   # guard fires
    assert read(tmp_path, "grant", session="sess-a") == "aegis-3"  # and does not over-fire
    assert read(tmp_path, "grant") == "aegis-3"  # caller declining the guard still reads


def test_stale_plate_is_unknown_and_fresh_is_not(tmp_path):
    publish(tmp_path, "grant", _Item("aegis-4"), _now=1000.0)
    assert read(tmp_path, "grant", newer_than=2000.0) is None      # written before session
    assert read(tmp_path, "grant", newer_than=500.0) == "aegis-4"  # written after


def test_guards_compose(tmp_path):
    publish(tmp_path, "grant", _Item("aegis-5"), session="s1", _now=1000.0)
    assert read(tmp_path, "grant", session="s1", newer_than=500.0) == "aegis-5"
    assert read(tmp_path, "grant", session="s1", newer_than=2000.0) is None
    assert read(tmp_path, "grant", session="s2", newer_than=500.0) is None


@pytest.mark.parametrize("bad", [
    {"item": None, "at": 1, "session": None},   # explicit empty plate
    {"item": "", "at": 1, "session": None},     # empty string is not an id
    {"item": 42, "at": 1, "session": None},     # wrong type
    ["not", "a", "dict"],                       # wrong container
])
def test_non_item_payloads_are_unknown(tmp_path, bad):
    p = plate_path(tmp_path, "grant")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(bad))
    assert read(tmp_path, "grant") is None


# --- fail-silence ----------------------------------------------------------

def test_publish_never_raises_on_an_unwritable_root(tmp_path):
    """A root it cannot write must not turn `st anchor` into a traceback.

    THE MECHANISM IS NOT A CHMOD, and that is the point. This used to make the
    directory 0o500 and assert the write was refused — which is a no-op for
    uid 0, so under root the write SUCCEEDED and the test failed on a clean
    tree. Worse than failing: had the assertion been the other way round it
    would have been VACUOUS, exercising the success path while claiming to
    exercise the failure path, and reporting green about coverage it did not
    have.

    A path component that is a regular FILE cannot be turned into a directory
    by anybody, root included — `mkdir(parents=True)` raises whatever the uid.
    Same fault class (the tree cannot be created), asserted by a means the
    kernel enforces for every caller rather than one it waives for a
    privileged one.
    """
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("a file where the crew tree needs a directory")
    assert publish(blocked, "grant", _Item("aegis-6")) is False


def test_publish_never_raises_when_permissions_forbid_it(tmp_path):
    """The chmod case, kept — it is the fault operators actually hit — but
    SKIPPED rather than silently waived where the bits do not apply. An honest
    "not measured here" is worth more than a pass that means nothing, which is
    what this assertion was under root."""
    import os
    import pytest
    if os.geteuid() == 0:
        pytest.skip("root ignores permission bits; see the file-in-the-path "
                    "case above, which holds for every uid")
    root = tmp_path / "ro"
    root.mkdir()
    root.chmod(0o500)
    try:
        assert publish(root, "grant", _Item("aegis-6")) is False
    finally:
        root.chmod(0o700)


def test_failed_publish_leaves_no_debris(tmp_path):
    """A partial write must not leave .plate-*.tmp files accumulating in the
    crew tree — a reader globbing the directory would see them, and they grow
    without bound on a persistently failing host."""
    publish(tmp_path, "grant", _Item("aegis-7"))
    d = plate_path(tmp_path, "grant").parent
    assert list(d.glob(".plate-*.tmp")) == []


def test_read_never_raises_on_a_directory_in_the_files_place(tmp_path):
    p = plate_path(tmp_path, "grant")
    p.mkdir(parents=True)
    assert read(tmp_path, "grant") is None


def test_the_file_in_the_path_case_is_not_vacuous(tmp_path):
    """The control for the mechanism above. A test that asserts `is False` is
    only worth something if the same call returns True when the tree IS
    creatable — otherwise it would pass against a publish that had simply
    stopped working."""
    assert publish(tmp_path, "grant", _Item("aegis-6")) is True


# --- session scoping (aegis-368cu.7) -------------------------------------
#
# `st anchor` stamps its own session so a reader can abstain on a plate left
# by a session that has since died. `st go` must NOT: a dispatcher cannot know
# the recipient's session, so it writes null, meaning "not session-scoped".
#
# The load-bearing case is `null stored + reader supplies a session`. Rejecting
# that would make every DISPATCHED plate unreadable the moment yupana starts
# passing a session — a staleness guard turned into a total attribution outage.
# It was the pre-existing behaviour and is what this asserts against.


def test_own_session_reads_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-abc")
    assert own_session() == "sess-abc"


def test_own_session_none_outside_a_harness(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    assert own_session() is None
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "   ")
    assert own_session() is None, "whitespace is not a session"


def test_session_scoped_plate_is_read_by_its_own_session(tmp_path):
    publish_id(tmp_path, "kelly", "aegis-1", session="sess-abc")
    assert read(tmp_path, "kelly", session="sess-abc") == "aegis-1"


def test_session_scoped_plate_abstains_for_a_different_session(tmp_path):
    publish_id(tmp_path, "kelly", "aegis-1", session="sess-abc")
    assert read(tmp_path, "kelly", session="sess-dead") is None


def test_dispatcher_plate_is_readable_by_any_session(tmp_path):
    """The regression this whole change turns on."""
    publish_id(tmp_path, "kelly", "aegis-2", session=None)
    assert read(tmp_path, "kelly", session="sess-anything") == "aegis-2"
    assert read(tmp_path, "kelly") == "aegis-2"


def test_session_scoped_plate_readable_when_reader_has_no_session(tmp_path):
    publish_id(tmp_path, "kelly", "aegis-3", session="sess-abc")
    assert read(tmp_path, "kelly") == "aegis-3"
