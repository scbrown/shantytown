"""`st go --worktree` — auto-provision an isolated worktree at dispatch, and
compose with keep-current (internal-ref + internal-ref).

The worktrees bug's acceptance is "two agents DISPATCHED to the same project repo
get separate worktrees, and st does it — not the agent by hand." So the dispatch
must: provision the agent's worktree, deliver its path IN the payload (same
atomicity as --note), REFUSE if it cannot isolate (dispatching shared-repo work
with no worktree is the clobber bug, not a fallback), and — dry-run — create
NOTHING. The keep-current sibling ff-pulls a CLONE; a worktree is on wt/<agent>,
so its refresh is rebase-onto-origin/main, proven here directly.
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

import pytest

import shantytown.cli as cli
from shantytown.cli import main, OK, REFUSED
from shantytown.tmux import NullPanes


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / ".shanty"
    (root / "crew").mkdir(parents=True)
    # No `workspace` on the card, so keep-current is a no-op — this test isolates
    # the worktree behaviour from the clone-pull.
    (root / "crew" / "ellie.json").write_text(json.dumps({"role": "worker", "pane": "%5"}))
    (root / "items").mkdir()
    (root / "items" / "item-1.json").write_text(
        json.dumps({"title": "Restore the den", "status": "open"}))
    return root


def _shared_repo(tmp_path: Path, name="proj") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("one\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "one")
    return repo


# --- st go --worktree, at the CLI ---------------------------------------------

def test_go_worktree_provisions_and_delivers_the_path(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path)
    repo = _shared_repo(tmp_path)
    panes = NullPanes(screen="")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: panes)
    rc = main(["--root", str(root), "go", "item-1", "ellie", "--worktree", str(repo)])
    assert rc == OK
    wt = repo.parent / "proj-wt" / "ellie"
    assert wt.is_dir(), "the worktree was not provisioned"
    out = capsys.readouterr().out
    assert "worktree:" in out and str(wt) in out
    # the path rode INTO the one dispatch payload (atomicity, like --note)
    assert panes.sent, "nothing was dispatched"
    _pane, text = panes.sent[-1]
    assert str(wt) in text, f"worktree path did not ride the dispatch: {text!r}"


def test_go_worktree_refuses_when_it_cannot_isolate(tmp_path, monkeypatch, capsys):
    # A --worktree target with no shared checkout: isolation is impossible, so the
    # dispatch is REFUSED — never degraded to the shared checkout (the bug).
    root = _root(tmp_path)
    notrepo = tmp_path / "notrepo"
    notrepo.mkdir()
    panes = NullPanes(screen="")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: panes)
    rc = main(["--root", str(root), "go", "item-1", "ellie", "--worktree", str(notrepo)])
    assert rc == REFUSED
    assert panes.sent == [], "refused dispatches must send nothing"
    assert "worktree" in capsys.readouterr().err
    # and the item was NOT marked in progress
    assert json.loads((root / "items" / "item-1.json").read_text())["status"] == "open"


def test_go_worktree_dry_run_creates_nothing(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path)
    repo = _shared_repo(tmp_path)
    panes = NullPanes(screen="")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: panes)
    rc = main(["--root", str(root), "go", "item-1", "ellie",
               "--worktree", str(repo), "--dry-run"])
    assert rc == OK
    assert not (repo.parent / "proj-wt" / "ellie").exists(), "dry-run created a worktree"
    assert panes.sent == [], "dry-run sent a dispatch"
    assert "would provision worktree" in capsys.readouterr().out


# --- _refresh_worktree: rebase (not ff-pull), and never over dirt -------------

def _worktree_off(tmp_path):
    """A shared repo whose origin/main is ONE commit ahead of a worktree on
    wt/ellie — the exact 'behind worktree' _refresh_worktree must rebase."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "t@example.invalid")
    _git(origin, "config", "user.name", "t")
    (origin / "a.txt").write_text("one\n")
    _git(origin, "add", "a.txt"); _git(origin, "commit", "-q", "-m", "one")
    shared = tmp_path / "proj"
    _git(tmp_path, "clone", "-q", str(origin), str(shared))
    _git(shared, "config", "user.email", "t@example.invalid")
    _git(shared, "config", "user.name", "t")
    wt = shared.parent / "proj-wt" / "ellie"
    wt.parent.mkdir(parents=True)
    _git(shared, "worktree", "add", "-b", "wt/ellie", str(wt), "origin/main")
    # origin advances; the worktree is now one commit behind origin/main
    (origin / "a.txt").write_text("two\n")
    _git(origin, "commit", "-q", "-am", "two")
    _git(shared, "fetch", "-q", "origin")
    return shared, wt


def test_refresh_worktree_rebases_a_clean_behind_worktree(tmp_path):
    _shared, wt = _worktree_off(tmp_path)
    warn = cli._refresh_worktree(wt)
    assert warn is None, f"a clean rebase should not warn: {warn}"
    assert (wt / "a.txt").read_text() == "two\n", "the worktree was not brought current"


def test_refresh_worktree_keeps_a_dirty_worktree_and_says_so(tmp_path):
    _shared, wt = _worktree_off(tmp_path)
    (wt / "a.txt").write_text("local edit\n")     # uncommitted work
    warn = cli._refresh_worktree(wt)
    assert warn and "local changes" in warn
    assert (wt / "a.txt").read_text() == "local edit\n", "dirty work must NOT be rebased away"


def test_refresh_worktree_on_a_non_repo_is_a_string_not_a_crash(tmp_path):
    warn = cli._refresh_worktree(tmp_path / "nope")
    assert isinstance(warn, str)                  # never raises
