"""An in_progress bead parked by a future defer_until is not an active anchor
(aegis-1d3fze).

Tend's haul reads the in_progress set directly, not through the plate, so the
status field alone decided whether it served a bead. sattler's 09-25 bulk
re-defer wrote defer_until and left status in_progress. Tend then re-served
aegis-rgvout to wu inside the park. These tests run against the real br CLI,
because the defect is how br represents a park, and a stub would restate that
representation rather than test it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import subprocess

import pytest

from shantytown import feed_check
from shantytown.br import BrTracker, in_progress
from shantytown.inbox import drop_parked


BR = shutil.which("br")


@pytest.fixture
def br_store(tmp_path, monkeypatch):
    if not BR or not Path(BR).is_file():
        pytest.skip("br binary is not installed")
    store = tmp_path / "store"
    store.mkdir()
    subprocess.run([BR, "init", "--prefix", "test", "--no-auto-import",
                    "--no-auto-flush"], cwd=store, check=True,
                   capture_output=True, text=True)
    monkeypatch.setenv("SHANTY_BR_BIN", BR)
    return store


def _stamp(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parked_and_control(tracker):
    parked = tracker.create("parked in flight", assignee="wu", priority=2)
    tracker.update(parked.id, status="in_progress")
    tracker.update(parked.id, defer_until=_stamp(timedelta(days=1)))
    lapsed = tracker.create("lapsed in flight", assignee="wu", priority=2)
    tracker.update(lapsed.id, status="in_progress")
    tracker.update(lapsed.id, defer_until=_stamp(-timedelta(days=1)))
    plain = tracker.create("plain in flight", assignee="wu", priority=2)
    tracker.update(plain.id, status="in_progress")
    return parked, lapsed, plain


def test_fixture_is_the_measured_shape(br_store):
    """CONTROL: the parked row really is in_progress with a future stamp in br.
    If br ever moves status to deferred on its own, the other tests would pass
    for the wrong reason, so this test fails loudly instead."""
    tracker = BrTracker(str(br_store))
    parked, _, _ = _parked_and_control(tracker)
    got = tracker.get(parked.id)
    assert got.status == "in_progress"
    assert got.defer_until and got.defer_until > _stamp(timedelta(0))


def test_parked_in_progress_is_not_an_active_anchor(br_store):
    tracker = BrTracker(str(br_store))
    parked, lapsed, plain = _parked_and_control(tracker)
    ids = {r["id"] for r in in_progress(tracker)}
    assert parked.id not in ids
    # A lapsed park and a plain in_progress bead are still active: the filter
    # removes only what is parked now, not everything that was ever deferred.
    assert {lapsed.id, plain.id} <= ids


def test_both_haul_readers_agree(br_store):
    """adapter.active() feeds tend's haul; queue_state() feeds Rule Zero."""
    tracker = BrTracker(str(br_store))
    parked, lapsed, plain = _parked_and_control(tracker)
    adapter = feed_check.TrackerAdapter.__new__(feed_check.TrackerAdapter)
    adapter.tracker, adapter.kind = tracker, "br"
    via_adapter = {r["id"] for r in adapter.active().exact()}
    _, active = feed_check.queue_state(None, None, tracker=tracker)
    via_rule_zero = {r["id"] for r in active}
    assert via_adapter == via_rule_zero
    assert parked.id not in via_adapter
    assert {lapsed.id, plain.id} <= via_adapter


def test_served_again_once_the_park_lapses():
    row = {"id": "x", "status": "in_progress",
           "defer_until": "2026-10-01T13:00:00Z"}
    before = datetime(2026, 10, 1, 12, 59, tzinfo=timezone.utc)
    after = datetime(2026, 10, 1, 13, 1, tzinfo=timezone.utc)
    assert drop_parked([row], now=before) == []
    assert drop_parked([row], now=after) == [row]


@pytest.mark.parametrize("value", [None, "", "soon", "not-a-date"])
def test_unreadable_or_absent_stamp_stays_active(value):
    """Fail toward WORKABLE, the same rule as _deferred_until_is_future: a stamp
    we cannot read must not silently withhold an agent's own work."""
    row = {"id": "x", "status": "in_progress", "defer_until": value}
    assert drop_parked([row]) == [row]
