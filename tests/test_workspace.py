"""ensure_workspace — the workspace leg of #5.

Three outcomes, all with a positive AND a negative control: present -> untouched,
absent -> cloned, cannot -> REFUSED. The one that matters most is the refusal: a
launcher that cannot refuse launches agents into directories that do not exist,
and the failure shows up as shell noise inside a pane that already came up.
"""
from __future__ import annotations
import subprocess
from pathlib import Path

import pytest

from shantytown.protocols import Agent
from shantytown.workspace import WorkspaceError, ensure_workspace, git_clone


def _fake_clone(marker="cloned"):
    """A cloner that records its calls and produces a real directory."""
    calls = []

    def clone(source, dest):
        calls.append((source, Path(dest)))
        Path(dest).mkdir(parents=True)
        (Path(dest) / marker).write_text(source)

    clone.calls = calls
    return clone


# --- no workspace elected: nothing to ensure, and that is not a failure -------

def test_no_workspace_returns_none_and_clones_nothing():
    clone = _fake_clone()
    assert ensure_workspace(Agent(name="ellie"), clone=clone) is None
    assert clone.calls == []


# --- present: idempotent, and the contents are NEVER touched -----------------

def test_present_workspace_is_returned_untouched(tmp_path):
    ws = tmp_path / "crew" / "ellie"
    ws.mkdir(parents=True)
    (ws / "CLAUDE.md").write_text("uncommitted work")
    clone = _fake_clone()

    card = Agent(name="ellie", workspace=str(ws), workspace_source="git@x:repo.git")
    assert ensure_workspace(card, clone=clone) == str(ws)
    # A source on the card must not tempt it into re-cloning or syncing: an
    # agent's workspace holds uncommitted work.
    assert clone.calls == []
    assert (ws / "CLAUDE.md").read_text() == "uncommitted work"


def test_ensure_is_idempotent_across_runs(tmp_path):
    ws = tmp_path / "ellie"
    card = Agent(name="ellie", workspace=str(ws), workspace_source="src")
    clone = _fake_clone()
    first = ensure_workspace(card, clone=clone)
    second = ensure_workspace(card, clone=clone)
    assert first == second == str(ws)
    assert len(clone.calls) == 1, "the second run re-cloned an existing workspace"


# --- absent + source: cloned, and the path returned is real ------------------

def test_absent_workspace_is_cloned(tmp_path):
    ws = tmp_path / "nested" / "ellie"
    clone = _fake_clone()
    card = Agent(name="ellie", workspace=str(ws), workspace_source="git@x:repo.git")

    got = ensure_workspace(card, clone=clone)

    assert got == str(ws)
    assert ws.is_dir(), "returned a path that does not exist"
    assert (ws / "cloned").read_text() == "git@x:repo.git"
    assert len(clone.calls) == 1
    src, dest = clone.calls[0]
    assert src == "git@x:repo.git"
    assert dest != ws, "cloned straight onto the final path — a partial clone would stick"


def test_workspace_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    card = Agent(name="ellie", workspace="~/ws", workspace_source="src")
    assert ensure_workspace(card, clone=_fake_clone()) == str(tmp_path / "ws")


# --- cannot: REFUSE, and leave nothing behind --------------------------------

def test_absent_with_no_source_refuses(tmp_path):
    ws = tmp_path / "ellie"
    card = Agent(name="ellie", workspace=str(ws))
    with pytest.raises(WorkspaceError) as e:
        ensure_workspace(card, clone=_fake_clone())
    assert "workspace_source" in str(e.value)
    assert not ws.exists(), "refused and still created something"


def test_workspace_path_that_is_a_file_refuses(tmp_path):
    ws = tmp_path / "ellie"
    ws.write_text("not a directory")
    card = Agent(name="ellie", workspace=str(ws), workspace_source="src")
    with pytest.raises(WorkspaceError) as e:
        ensure_workspace(card, clone=_fake_clone())
    assert "not a directory" in str(e.value)


def test_failed_clone_leaves_no_partial_workspace(tmp_path):
    """The trap this guards: a half-clone at the final path would make the NEXT
    run take the present-and-idempotent branch and launch into a broken tree."""
    ws = tmp_path / "ellie"

    def dies_partway(source, dest):
        Path(dest).mkdir(parents=True)
        (Path(dest) / "half").write_text("")
        raise WorkspaceError("network died")

    card = Agent(name="ellie", workspace=str(ws), workspace_source="src")
    with pytest.raises(WorkspaceError):
        ensure_workspace(card, clone=dies_partway)
    assert not ws.exists(), "a partial clone was left at the workspace path"
    assert list(tmp_path.iterdir()) == [], "staging debris left behind"


def test_cloner_that_lies_about_success_refuses(tmp_path):
    """A cloner that exits 0 and produces nothing must not yield a path."""
    ws = tmp_path / "ellie"
    card = Agent(name="ellie", workspace=str(ws), workspace_source="src")
    with pytest.raises(WorkspaceError) as e:
        ensure_workspace(card, clone=lambda s, d: None)
    assert "produced no directory" in str(e.value)
    assert not ws.exists()


# --- the real cloner, against a real local git repo (no network) --------------

def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_git_clone_clones_a_real_repo(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", cwd=origin)
    (origin / "README.md").write_text("hello")
    _git("add", "-A", cwd=origin)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=origin)

    card = Agent(name="ellie", workspace=str(tmp_path / "ws"),
                 workspace_source=str(origin))
    got = ensure_workspace(card)

    assert Path(got, "README.md").read_text() == "hello"
    assert Path(got, ".git").exists()


def test_git_clone_failure_raises_workspace_error(tmp_path):
    with pytest.raises(WorkspaceError) as e:
        git_clone(str(tmp_path / "nope"), tmp_path / "dest")
    assert "failed" in str(e.value)
