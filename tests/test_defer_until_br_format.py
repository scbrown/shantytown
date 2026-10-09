"""The --until contract must agree with the installed br update validator.

The first four values are the aegis-z7zllh field fixture. A fifth proves the
documented time-bearing spelling reaches br and retains its hour.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from shantytown.br import BrTracker
from shantytown.dispatch import DeferRefused, Dispatcher
from shantytown.files import FilesRegistry
from shantytown.tmux import NullPanes
from shantytown import br as br_adapter, deferrals


VALUES = [
    ("2026-09-21T23:05Z", False),
    ("2026-09-21T23:05:00", False),
    ("+24h", False),
    ("2026-09-21", True),
    ("2026-09-21T23:05:00Z", True),
]


@pytest.mark.real_store
@pytest.mark.skipif(shutil.which("br") is None, reason="requires br CLI")
@pytest.mark.parametrize("until,accepted", VALUES)
def test_dry_run_and_write_agree_with_real_br(tmp_path, until, accepted):
    def br(*args):
        return subprocess.run(["br", *args], cwd=tmp_path, capture_output=True,
                              text=True, check=True).stdout

    br("init", "--prefix", "fmt")
    bead = re.search(r"fmt-[a-z0-9]+", br("create", "format probe", "-t", "task")).group()
    tracker = BrTracker(repo=str(tmp_path))
    dispatch = Dispatcher(FilesRegistry(tmp_path / "crew"), tracker, NullPanes())
    before = tracker.get(bead)

    if not accepted:
        for dry_run in (True, False):
            with pytest.raises(DeferRefused, match="YYYY-MM-DDTHH:MM:SSZ"):
                dispatch.defer(bead, "human", "wait for the window", until=until,
                               dry_run=dry_run)
        assert tracker.get(bead).defer_until == before.defer_until
        assert tracker.get(bead).status == before.status
        return

    planned = dispatch.defer(bead, "human", "wait for the window", until=until,
                             dry_run=True)
    assert planned.condition == until
    assert tracker.get(bead).defer_until == before.defer_until

    dispatch.defer(bead, "human", "wait for the window", until=until)
    stored = tracker.get(bead)
    assert stored.status == "deferred"
    if "T" in until:
        assert stored.defer_until == until
    else:
        assert stored.defer_until.startswith(until + "T")


@pytest.mark.real_store
@pytest.mark.skipif(shutil.which("br") is None, reason="requires br CLI")
def test_real_br_same_day_timestamp_survives_into_deferral_sweep(tmp_path):
    def br(*args):
        return subprocess.run(["br", *args], cwd=tmp_path, capture_output=True,
                              text=True, check=True).stdout

    br("init", "--prefix", "fmt")
    created = json.loads(br("create", "timestamp probe", "--json"))
    if isinstance(created, list):
        created = created[0]
    bead = created["id"]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    deadline = now + timedelta(hours=2)
    until = deadline.isoformat().replace("+00:00", "Z")
    tracker = BrTracker(repo=str(tmp_path))
    tracker.update(bead, defer_until=until)
    assert tracker.get(bead).defer_until == until
    rows = br_adapter.deferred(tracker)
    assert any(row["id"] == bead and row["defer_until"] == until for row in rows)
    assert deferrals.evaluate(rows, now) == []
    assert deferrals.evaluate(rows, deadline - timedelta(microseconds=1)) == []
    findings = deferrals.evaluate(rows, deadline)
    assert len(findings) == 1 and findings[0].bead == bead
    assert findings[0].lapsed_at == deadline
