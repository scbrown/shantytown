"""A container name must select the worktree provisioned from its crew clone."""
from pathlib import Path

import pytest

from shantytown.cli import main, OK, REFUSED
from tests.test_push_every_remote import _git


@pytest.fixture(params=["gt", "workspace"])
def crew_container(tmp_path, monkeypatch, request):
    gt = tmp_path / "gt"
    container = tmp_path / request.param / "beads_aegis"
    source = container / "crew" / "tim"
    source.mkdir(parents=True)
    remote = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    _git(source, "init", "-q", "-b", "main")
    _git(source, "config", "user.name", "Fixture")
    _git(source, "config", "user.email", "fixture@example.invalid")
    (source / "control").write_text("base\n")
    _git(source, "add", "control")
    _git(source, "commit", "-qm", "base")
    _git(source, "remote", "add", "origin", str(remote))
    _git(source, "config", "remote.origin.st-push-allowed", "true")
    _git(source, "push", "-qu", "origin", "main")
    monkeypatch.setenv("GT_ROOT", str(gt))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    assert main(["repo", "worktree", str(source), "tim"]) == OK
    tree = container / "crew" / "tim-wt" / "tim"
    _git(tree, "switch", "-qc", "tim/6bw")
    (tree / "change").write_text("candidate\n")
    _git(tree, "add", "change")
    _git(tree, "commit", "-qm", "candidate")
    monkeypatch.chdir(tree)
    return container, source, tree, remote


def _push():
    return main(["repo", "push", "beads_aegis", "tim",
                 "--source-branch", "tim/6bw", "--branch", "main"])


def test_same_argv_publishes_from_provisioned_crew_tree(crew_container):
    _, _, tree, remote = crew_container
    before = _git(remote, "rev-parse", "main")
    head = _git(tree, "rev-parse", "HEAD")
    assert head != before
    assert _push() == OK
    assert _git(remote, "rev-parse", "main") == head


def test_wrong_checked_out_source_still_refuses(crew_container, capsys):
    _, _, tree, remote = crew_container
    before = _git(remote, "rev-parse", "main")
    _git(tree, "switch", "-qc", "tim/other")
    assert _push() == REFUSED
    assert _git(remote, "rev-parse", "main") == before
    assert "differs from selected source" in capsys.readouterr().err


def test_unrelated_registered_tree_in_crew_slot_refuses(
        crew_container, tmp_path, monkeypatch, capsys):
    _, source, tree, remote = crew_container
    before = _git(remote, "rev-parse", "main")
    monkeypatch.chdir(tmp_path)
    _git(source, "worktree", "remove", str(tree))
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(remote), str(other))
    _git(other, "worktree", "add", "-qb", "tim/6bw", str(tree), "main")
    monkeypatch.chdir(tree)
    assert _push() == REFUSED
    assert _git(remote, "rev-parse", "main") == before
    assert "crew worktree" in capsys.readouterr().err


def test_crew_and_legacy_candidates_are_ambiguous(crew_container, capsys):
    container, _, _, remote = crew_container
    before = _git(remote, "rev-parse", "main")
    legacy = container.parent / "beads_aegis-wt" / "tim"
    legacy.mkdir(parents=True)
    assert _push() == REFUSED
    assert _git(remote, "rev-parse", "main") == before
    assert "ambiguous worktree" in capsys.readouterr().err


def test_missing_crew_tree_names_actual_provisioning_command(
        crew_container, tmp_path, monkeypatch, capsys):
    _, source, tree, remote = crew_container
    before = _git(remote, "rev-parse", "main")
    monkeypatch.chdir(tmp_path)
    _git(source, "worktree", "remove", str(tree))
    assert _push() == REFUSED
    assert _git(remote, "rev-parse", "main") == before
    assert f"st repo worktree {source} tim" in capsys.readouterr().err
