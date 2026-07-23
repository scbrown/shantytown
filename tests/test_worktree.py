"""ensure_worktree / cleanup_worktree — per-agent worktrees for SHARED project
repos (internal-ref).

The point of the whole feature is that no agent ever runs `git worktree add` by
hand and no two agents write the same index/HEAD. So the tests pin: absent ->
provisioned, present -> untouched (idempotent), missing shared checkout ->
REFUSED, and — the one that matters most — cleanup NEVER discards a worktree that
holds work. The add/remove/holds-work git ops are injected so none of this needs a
real repo, same pattern as test_workspace.py.
"""
from __future__ import annotations
from pathlib import Path

import pytest

from shantytown.workspace import (WorkspaceError, cleanup_worktree,
                                  ensure_worktree, worktree_for)


def _fake_add(marker="wt"):
    """An adder that records its calls and produces a real worktree directory."""
    calls = []

    def add(shared, dest, agent, base):
        calls.append((Path(shared), Path(dest), agent, base))
        Path(dest).mkdir(parents=True)
        (Path(dest) / marker).write_text(f"{shared}::{agent}::{base}")

    add.calls = calls
    return add


def _shared_repo(tmp_path, name="quipu"):
    """A stand-in shared checkout: a dir with a .git marker (ensure_worktree only
    checks that .git exists; the real git work is the injected adder's job)."""
    repo = tmp_path / name
    (repo / ".git").mkdir(parents=True)
    return repo


# --- worktree_for: the layout contract, including -wt tolerance ---------------

def test_worktree_for_layout(tmp_path):
    repo = tmp_path / "quipu"
    assert worktree_for(repo, "billy") == tmp_path / "quipu-wt" / "billy"


def test_worktree_for_tolerates_being_handed_the_wt_dir(tmp_path):
    # handed the container or a sibling worktree, it still resolves to the SAME
    # per-agent path — so callers can pass either spelling without forking dirs.
    assert worktree_for(tmp_path / "quipu-wt", "billy") == tmp_path / "quipu-wt" / "billy"
    assert worktree_for(tmp_path / "quipu-wt" / "zia", "billy") == tmp_path / "quipu-wt" / "billy"


# --- ensure_worktree: absent -> provisioned ----------------------------------

def test_absent_worktree_is_provisioned(tmp_path):
    repo = _shared_repo(tmp_path)
    add = _fake_add()
    got = ensure_worktree(repo, "billy", add=add)
    assert got == str(tmp_path / "quipu-wt" / "billy")
    assert Path(got).is_dir()
    assert len(add.calls) == 1
    _, dest, agent, base = add.calls[0]
    assert agent == "billy" and base == "origin/main"


def test_present_worktree_is_returned_untouched(tmp_path):
    repo = _shared_repo(tmp_path)
    dest = tmp_path / "quipu-wt" / "billy"
    dest.mkdir(parents=True)
    add = _fake_add()
    got = ensure_worktree(repo, "billy", add=add)
    assert got == str(dest)
    assert add.calls == []               # idempotent: present means present


def test_ensure_worktree_is_idempotent_across_runs(tmp_path):
    repo = _shared_repo(tmp_path)
    add = _fake_add()
    first = ensure_worktree(repo, "billy", add=add)
    second = ensure_worktree(repo, "billy", add=add)
    assert first == second
    assert len(add.calls) == 1           # provisioned once, not twice


def test_two_agents_get_separate_worktrees(tmp_path):
    # THE acceptance: two agents on the same shared repo never share an index/HEAD.
    repo = _shared_repo(tmp_path)
    add = _fake_add()
    a = ensure_worktree(repo, "billy", add=add)
    b = ensure_worktree(repo, "zia", add=add)
    assert a != b
    assert a == str(tmp_path / "quipu-wt" / "billy")
    assert b == str(tmp_path / "quipu-wt" / "zia")


# --- ensure_worktree: refusals -----------------------------------------------

def test_no_shared_checkout_refuses(tmp_path):
    missing = tmp_path / "quipu"         # no .git
    missing.mkdir()
    with pytest.raises(WorkspaceError):
        ensure_worktree(missing, "billy", add=_fake_add())


def test_worktree_path_that_is_a_file_refuses(tmp_path):
    repo = _shared_repo(tmp_path)
    dest = tmp_path / "quipu-wt" / "billy"
    dest.parent.mkdir(parents=True)
    dest.write_text("not a dir")
    with pytest.raises(WorkspaceError):
        ensure_worktree(repo, "billy", add=_fake_add())


def test_adder_that_produces_nothing_refuses(tmp_path):
    repo = _shared_repo(tmp_path)

    def add_noop(shared, dest, agent, base):
        pass                             # reports success, makes no directory

    with pytest.raises(WorkspaceError):
        ensure_worktree(repo, "billy", add=add_noop)


# --- cleanup_worktree: never discard work ------------------------------------

def _fake_remove():
    calls = []

    def remove(shared, dest):
        calls.append((Path(shared), Path(dest)))
        import shutil
        shutil.rmtree(dest)

    remove.calls = calls
    return remove


def test_cleanup_removes_an_unchanged_worktree(tmp_path):
    repo = _shared_repo(tmp_path)
    dest = tmp_path / "quipu-wt" / "billy"
    dest.mkdir(parents=True)
    remove = _fake_remove()
    removed = cleanup_worktree(repo, "billy", remove=remove,
                               holds_work=lambda d, base: False)
    assert removed is True
    assert len(remove.calls) == 1
    assert not dest.exists()


def test_cleanup_keeps_a_worktree_that_holds_work(tmp_path):
    repo = _shared_repo(tmp_path)
    dest = tmp_path / "quipu-wt" / "billy"
    dest.mkdir(parents=True)
    remove = _fake_remove()
    removed = cleanup_worktree(repo, "billy", remove=remove,
                               holds_work=lambda d, base: True)
    assert removed is False
    assert remove.calls == []            # holds work -> NEVER removed
    assert dest.is_dir()


def test_cleanup_absent_worktree_is_a_noop(tmp_path):
    repo = _shared_repo(tmp_path)
    remove = _fake_remove()
    removed = cleanup_worktree(repo, "billy", remove=remove,
                               holds_work=lambda d, base: False)
    assert removed is False
    assert remove.calls == []
