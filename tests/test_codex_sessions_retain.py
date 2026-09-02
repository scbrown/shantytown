"""Retention for the DURABLE codex sessions root (aegis-4tbo84).

aegis-tx4fiy moved codex `sessions/` off tmpfs so a rollout survives a reboot.
That removed a janitor nobody had to think about: tmpfs self-cleaned, disk does
not. This is the replacement, and its gate is the MIRROR of the archive's.

  st-history-retain.sh   prunes the ARCHIVE, refuses when the archive is the
                         ONLY copy.
  this one               prunes the SOURCE, refuses unless the archive HAS a
                         copy.

Same principle from opposite ends: never delete the last copy of a session.
"""
from __future__ import annotations
import os
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "st-codex-sessions-retain.sh"


def _run(root: Path, archive: Path, *args, **env):
    return subprocess.run(
        [str(SCRIPT), *args], capture_output=True, text=True, timeout=120,
        env={**os.environ, "ST_CODEX_SESSIONS_ROOT": str(root),
             "ST_HISTORY_DIR": str(archive),
             "ST_CODEX_KEEP_MIN": env.get("keep_min", "0"),
             "ST_CODEX_KEEP_DAYS": env.get("keep_days", "30")})


def _rollout(d: Path, name: str, body: str, age_days: int = 0) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text(body)
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(f, (old, old))
    return f


def test_an_UNARCHIVED_rollout_is_never_pruned(tmp_path):
    """THE acceptance criterion. Nothing may be deleted that is not
    demonstrably present elsewhere — this file would be the only copy."""
    root, archive = tmp_path / "state", tmp_path / "archive"
    archive.mkdir()
    f = _rollout(root / "ellie", "rollout-A.jsonl", "only copy", age_days=999)

    out = _run(root, archive, "--apply")

    assert f.is_file(), "the only copy of a session was deleted"
    assert "unarchived=1 (NEVER pruned)" in out.stdout, out.stdout


def test_an_archived_and_old_rollout_is_pruned(tmp_path):
    """The positive control: without this the test above passes trivially for a
    script that never prunes anything."""
    root, archive = tmp_path / "state", tmp_path / "archive"
    f = _rollout(root / "ellie", "rollout-B.jsonl", "body", age_days=999)
    _rollout(archive / "ellie", "rollout-B.jsonl", "body")

    out = _run(root, archive, "--apply")

    assert not f.exists(), f"an archived, aged rollout was not pruned: {out.stdout}"
    assert "pruned=1" in out.stdout


def test_a_rollout_whose_ARCHIVE_COPY_IS_BEHIND_is_kept(tmp_path):
    """Capture re-copies only when the source GROWS, so an archive copy can be a
    real file with the right name and still be stale. Pruning on name alone
    would silently truncate the session to whatever was last captured — a loss
    that leaves a plausible-looking file behind."""
    root, archive = tmp_path / "state", tmp_path / "archive"
    f = _rollout(root / "ellie", "rollout-C.jsonl", "a much longer live body",
                 age_days=999)
    _rollout(archive / "ellie", "rollout-C.jsonl", "short")   # captured earlier

    out = _run(root, archive, "--apply")

    assert f.is_file(), "pruned against a stale archive copy — the tail is lost"
    assert "archive-behind=1" in out.stdout


def test_dry_run_is_the_default(tmp_path):
    root, archive = tmp_path / "state", tmp_path / "archive"
    f = _rollout(root / "ellie", "rollout-D.jsonl", "body", age_days=999)
    _rollout(archive / "ellie", "rollout-D.jsonl", "body")

    out = _run(root, archive)          # no --apply

    assert f.is_file(), "deleted without --apply"
    assert "WOULD prune" in out.stdout and "dry run" in out.stdout


def test_a_young_rollout_is_kept_even_when_archived(tmp_path):
    root, archive = tmp_path / "state", tmp_path / "archive"
    f = _rollout(root / "ellie", "rollout-E.jsonl", "body", age_days=1)
    _rollout(archive / "ellie", "rollout-E.jsonl", "body")

    out = _run(root, archive, "--apply")

    assert f.is_file()
    assert "younger-than-30d=1" in out.stdout


def test_the_newest_are_kept_by_the_floor(tmp_path):
    root, archive = tmp_path / "state", tmp_path / "archive"
    for i in range(4):
        _rollout(root / "ellie", f"rollout-{i}.jsonl", "body", age_days=100 + i)
        _rollout(archive / "ellie", f"rollout-{i}.jsonl", "body")

    out = _run(root, archive, "--apply", keep_min="2")

    left = sorted(p.name for p in (root / "ellie").rglob("rollout-*.jsonl"))
    assert len(left) == 2, f"floor not applied: {left} / {out.stdout}"
    # newest-first ordering means the two NEWEST survive
    assert left == ["rollout-0.jsonl", "rollout-1.jsonl"], left


def test_a_MISSING_ARCHIVE_refuses_rather_than_deleting_everything(tmp_path):
    """An unreadable instrument must never read as 'no copy exists'. That
    inversion would turn a missing archive into permission to delete the lot."""
    root = tmp_path / "state"
    f = _rollout(root / "ellie", "rollout-F.jsonl", "body", age_days=999)

    done = _run(root, tmp_path / "does-not-exist", "--apply")

    assert done.returncode == 2, done.stdout
    assert f.is_file(), "deleted while the archive could not be consulted"
    assert "REFUSING" in done.stdout


def test_an_absent_root_is_a_noop_not_an_error(tmp_path):
    """The root does not exist until a codex card relaunches — tx4fiy is
    prospective. A cron line must not go red for that."""
    archive = tmp_path / "archive"; archive.mkdir()
    done = _run(tmp_path / "nothing-here", archive, "--apply")
    assert done.returncode == 0
    assert "nothing to do" in done.stdout
