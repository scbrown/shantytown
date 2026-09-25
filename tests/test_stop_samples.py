"""Privacy, finite collection and failures must be real, not banner checks."""
import json
import os
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from shantytown import stop_samples as samples


def event(n=1):
    return {"event_id": f"ev-{n}", "item": "task-1", "item_status": "in_progress"}


def read(root):
    return json.loads((root / "stop_samples/collection.json").read_text())


def test_private_bounded_and_unlabelled(tmp_path):
    samples.collect(tmp_path, event(), lambda: "é" * 6000, now=1)
    directory = tmp_path / "stop_samples"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in directory.iterdir())
    sample = read(tmp_path)["samples"][0]
    assert len(sample["pane_tail"].encode()) <= samples.TAIL_BYTES
    assert sample["tail_truncated"]
    assert sample["expected"] is None and sample["label_author"] is None
    assert sample["boundary_kind"] == "turn-stop-hook"
    assert sample["item_status"] == "in_progress"


def test_thirty_total_and_no_capture_after_full(tmp_path):
    for n in range(30):
        assert samples.collect(tmp_path, event(n), lambda: "tail", now=1) == "captured"
    def forbidden():
        pytest.fail("full collection must not read the pane")
    assert samples.collect(tmp_path, event(31), forbidden, now=2) == "full"
    assert len(read(tmp_path)["samples"]) == 30


def test_duplicate_does_not_capture_again(tmp_path):
    samples.collect(tmp_path, event(), lambda: "tail", now=1)
    assert samples.collect(tmp_path, event(), None, now=2) == "duplicate"


def test_expiry_removes_text_and_never_refills(tmp_path):
    samples.collect(tmp_path, event(), lambda: "sensitive", now=1)
    expired = 1 + samples.RETENTION_SECONDS
    assert samples.collect(tmp_path, None, now=expired) == "expired"
    assert read(tmp_path)["samples"] == []
    assert read(tmp_path)["closed"]
    assert "sensitive" not in (tmp_path / "stop_samples/collection.json").read_text()
    samples.collect(tmp_path, event(2), lambda: pytest.fail("expired"), now=expired+1)
    assert read(tmp_path)["samples"] == []


@pytest.mark.parametrize("leaf", ["lock", "collection.json"])
def test_symlink_never_followed(tmp_path, leaf):
    directory = tmp_path / "stop_samples"
    directory.mkdir(mode=0o700)
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    (directory / leaf).symlink_to(victim)
    with pytest.raises(OSError):
        samples.collect(tmp_path, event(), lambda: "tail")
    assert victim.read_text() == "untouched"


def test_public_directory_refused(tmp_path):
    directory = tmp_path / "stop_samples"
    directory.mkdir(mode=0o755)
    with pytest.raises(ValueError):
        samples.collect(tmp_path, event(), lambda: "tail")
    assert not list(directory.iterdir())


@pytest.mark.parametrize("leaf", ["lock", "collection.json"])
def test_fifo_refused_before_it_can_block_delivery(tmp_path, leaf):
    directory = tmp_path / "stop_samples"
    directory.mkdir(mode=0o700)
    os.mkfifo(directory / leaf, mode=0o600)
    result = subprocess.run(
        [sys.executable, "-m", "shantytown.stop_samples", "--root", str(tmp_path)],
        capture_output=True, text=True, timeout=3,
    )
    assert result.returncode == 1
    assert result.stdout.strip() == "stop samples unavailable: ValueError"


def test_corrupt_collection_is_not_replaced(tmp_path):
    samples.collect(tmp_path, event(), lambda: "tail", now=1)
    path = tmp_path / "stop_samples/collection.json"
    path.write_text("broken")
    with pytest.raises(ValueError):
        samples.collect(tmp_path, event(2), lambda: "tail", now=2)
    assert path.read_text() == "broken"


def test_public_existing_file_is_refused_without_chmod(tmp_path):
    samples.collect(tmp_path, event(), lambda: "tail", now=1)
    path = tmp_path / "stop_samples/collection.json"
    path.chmod(0o644)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        samples.collect(tmp_path, event(2), lambda: "new", now=2)
    assert path.read_bytes() == before
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_sweep_removes_interrupted_writer_staging(tmp_path):
    samples.collect(tmp_path, event(), lambda: "tail", now=1)
    staging = tmp_path / "stop_samples/.collection-interrupted"
    staging.write_text("old sensitive bytes")
    staging.chmod(0o600)
    assert samples.collect(tmp_path, None, now=2) == "retained"
    assert not staging.exists()


def test_empty_or_failed_pane_consumes_no_sample(tmp_path):
    assert samples.collect(tmp_path, event(), lambda: "") == "unavailable"
    def failed():
        raise subprocess.TimeoutExpired("capture", 2)
    with pytest.raises(subprocess.TimeoutExpired):
        samples.collect(tmp_path, event(), failed)
    assert not (tmp_path / "stop_samples/collection.json").exists()


def test_concurrent_collection_never_exceeds_limit(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda n: samples.collect(tmp_path, event(n), lambda: "tail"), range(80)))
    assert len(read(tmp_path)["samples"]) <= 30
    assert set(results) <= {"captured", "full", "busy"}


def test_tmux_capture_passes_deadline(monkeypatch):
    from shantytown.tmux import Tmux
    calls = []
    def run(*args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout="tail")
    monkeypatch.setattr(subprocess, "run", run)
    assert Tmux().capture("pane", timeout=2) == "tail"
    assert calls[-1]["timeout"] == 2
    Tmux().capture("pane")
    assert "timeout" not in calls[-1]
