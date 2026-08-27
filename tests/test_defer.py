"""`st defer` records blocker KIND at the moment work leaves the ready pool."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from shantytown import cli
from shantytown.beads import BeadsTracker
from shantytown.dispatch import BLOCKER_KIND_LABELS, DeferRefused, Dispatcher
from shantytown.files import FilesRegistry, FilesTracker
from shantytown.tmux import NullPanes


@pytest.fixture
def world(tmp_path: Path):
    tracker = FilesTracker(tmp_path / "items")
    tracker.update("item-1", title="Deploy the service", status="in_progress",
                   assignee="kelly", labels=["security", "blocked:human"])
    dispatcher = Dispatcher(FilesRegistry(tmp_path / "crew"), tracker, NullPanes())
    return dispatcher, tracker


@pytest.mark.parametrize("kind,label", BLOCKER_KIND_LABELS.items())
def test_every_declared_kind_is_recorded_with_status_and_reason(world, kind, label):
    dispatcher, tracker = world

    result = dispatcher.defer("item-1", kind, "referent: named; re-test: explicit")

    raw = json.loads(tracker._path("item-1").read_text())
    assert raw["status"] == "deferred"
    assert set(raw["labels"]) & set(BLOCKER_KIND_LABELS.values()) == {label}
    assert "referent: named" in raw["notes"]
    assert result.label == label and result.track_attempts == 1


def test_reclassification_removes_the_old_kind_in_the_same_write(world):
    dispatcher, tracker = world

    dispatcher.defer("item-1", "access", "re-test the credential")

    raw = json.loads(tracker._path("item-1").read_text())
    assert "blocked:access" in raw["labels"]
    assert "blocked:human" not in raw["labels"]


def test_empty_reason_and_closed_item_are_refused_without_writes(world):
    dispatcher, tracker = world
    before = tracker._path("item-1").read_bytes()
    with pytest.raises(DeferRefused):
        dispatcher.defer("item-1", "human", "   ")
    assert tracker._path("item-1").read_bytes() == before

    tracker.update("item-1", status="closed")
    closed = tracker._path("item-1").read_bytes()
    with pytest.raises(DeferRefused):
        dispatcher.defer("item-1", "human", "ask the owner")
    assert tracker._path("item-1").read_bytes() == closed


def test_dry_run_and_idempotent_repeat_do_not_append_notes(world):
    dispatcher, tracker = world
    before = tracker._path("item-1").read_bytes()
    dispatcher.defer("item-1", "external", "wait for 2026-09-01", dry_run=True)
    assert tracker._path("item-1").read_bytes() == before

    dispatcher.defer("item-1", "external", "wait for 2026-09-01")
    once = tracker._path("item-1").read_bytes()
    result = dispatcher.defer("item-1", "external", "wait for 2026-09-01")
    assert result.noop
    assert tracker._path("item-1").read_bytes() == once


def test_cli_requires_a_reason_file_and_reports_the_structured_label(tmp_path, capsys):
    items = tmp_path / "items"
    tracker = FilesTracker(items)
    tracker.update("item-1", title="x", status="open")
    reason = tmp_path / "reason.md"
    reason.write_text("referent: release event; re-test on publication")

    rc = cli.main(["--root", str(tmp_path), "defer", "item-1", "external",
                   "--reason-file", str(reason)])

    assert rc == 0
    assert "deferred as blocked:external" in capsys.readouterr().out
    item = tracker.get("item-1")
    assert (item.status, item.blocker_kind) == ("deferred", "blocked:external")


def test_beads_backend_sends_status_kind_cleanup_and_reason_in_one_write():
    class CapturingBeads(BeadsTracker):
        def __init__(self):
            super().__init__(repo=None)
            self.calls = []

        def _bd_for(self, item_id, *args):
            self.calls.append((item_id, args))
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    tracker = CapturingBeads()
    tracker.update("aegis-test", status="deferred",
                   blocker_kind="blocked:access",
                   defer_reason="re-test the credential")

    assert len(tracker.calls) == 1
    item_id, args = tracker.calls[0]
    assert item_id == "aegis-test"
    assert args[:2] == ("update", "aegis-test")
    assert "--status=deferred" in args
    assert "--add-label=blocked:access" in args
    assert "--append-notes=re-test the credential" in args
    assert {a.removeprefix("--remove-label=") for a in args
            if a.startswith("--remove-label=")} == {
                "blocked:bead", "blocked:human", "blocked:external",
                "parked:by-design"}
