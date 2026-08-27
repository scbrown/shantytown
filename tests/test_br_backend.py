"""The br backend exercises the real SQLite+JSONL CLI, never Dolt."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from shantytown import cli
from shantytown.br import BrTracker, items, plate, rows
from shantytown.tmux import NullPanes


BR = shutil.which("br") or "br"


@pytest.fixture
def br_store(tmp_path, monkeypatch):
    if not Path(BR).is_file():
        pytest.skip("br binary is not installed")
    store = tmp_path / "store"
    store.mkdir()
    subprocess.run([BR, "init", "--prefix", "test", "--no-auto-import",
                    "--no-auto-flush"], cwd=store, check=True,
                   capture_output=True, text=True)
    monkeypatch.setenv("SHANTY_BR_BIN", BR)
    return store


def _root(tmp_path):
    root = tmp_path / ".shanty"
    (root / "crew").mkdir(parents=True)
    (root / "crew" / "arnold.json").write_text(json.dumps(
        {"role": "worker", "pane": "%1"}))
    return root


def test_real_br_crud_readers_and_terminal_transition(br_store):
    tracker = BrTracker(str(br_store))
    made = tracker.create("backend proof", assignee="arnold", priority=1)
    assert tracker.get(made.id).priority == 1
    assert plate(tracker, "arnold").id == made.id
    assert [x.id for x in items(tracker)] == [made.id]
    assert [x["id"] for x in rows(tracker)] == [made.id]

    tracker.update(made.id, status="in_progress", blocker_kind="blocked:human",
                   defer_reason="measured")
    got = tracker.get(made.id)
    assert got.status == "in_progress" and got.blocker_kind == "blocked:human"
    tracker.update(made.id, status="closed")
    assert tracker.get(made.id).status == "closed"
    assert plate(tracker, "arnold") is None


def test_anchor_go_crew_and_durable_inbox_use_scratch_br(
        br_store, tmp_path, monkeypatch, capsys):
    root = _root(tmp_path)
    tracker = BrTracker(str(br_store))
    item = tracker.create("dispatch proof", priority=1)
    argv = ["--root", str(root), "--backend", "br", "--repo", str(br_store)]
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: NullPanes(screen=""))
    monkeypatch.setenv("SHANTY_AGENT", "arnold")

    assert cli.main([*argv, "go", item.id, "arnold"]) == cli.OK
    assert cli.main([*argv, "anchor", "arnold"]) == cli.OK
    assert item.id in capsys.readouterr().out
    assert cli.main([*argv, "crew"]) == cli.OK
    assert cli.main([*argv, "inbox", "-d", "arnold", "scratch message"]) == cli.OK
    delivered = capsys.readouterr().out
    assert "delivered to inbox" in delivered and "(br)" in delivered
    # The live send closes its durable pointer, so the normal open-item lister
    # intentionally omits it. The JSONL is the persistence proof.
    assert "inbox:" in (br_store / ".beads" / "issues.jsonl").read_text()
    assert tracker.get(item.id).status == "in_progress"


def test_deployment_keys_select_br_store(br_store, tmp_path, monkeypatch):
    root = _root(tmp_path)
    (root / "shantytown.toml").write_text(
        f'[env]\nSHANTY_BACKEND = "br"\nSHANTY_BR_REPO = "{br_store}"\n')
    args = type("Args", (), {"root": root, "backend": None, "repo": None})()
    tracker = cli._tracker(args)
    assert isinstance(tracker, BrTracker)
    assert tracker.repo == str(br_store.resolve())
