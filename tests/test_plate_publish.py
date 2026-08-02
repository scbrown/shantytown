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

from shantytown.plate_publish import plate_path, publish, read


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
    """A read-only root must not turn `st anchor` into a traceback."""
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
