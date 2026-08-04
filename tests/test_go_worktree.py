"""`st go --worktree` — auto-provision an isolated worktree at dispatch, and
compose with keep-current (aegis-h2rr + aegis-4zld).

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


# --- a second REMOTE holding work this tree does not have (aegis-96few) -------
#
# WHAT THESE ARE, PRECISELY: regression guards on CONSUMPTION, not proof of a fix.
#
# `upstream_ref` computes a divergence note, and tests/test_staleness.py already
# proves it PRODUCES one. Nothing asserted that a caller SHOWS it — and three call
# sites spell it `ref, _ =`, discarding it on exactly the branches that print a
# reassuring "current with <ref>" line. Measured 2026-08-04: the note does reach
# the operator today, but only because `_refresh_worktree` independently carries
# it; the discard sites are harmless by luck of a second path, not by design.
#
# So these lock in the OBSERVABLE — a second remote that is ahead reaches a human
# — without asserting which code path delivered it. If `_refresh_worktree` ever
# stops carrying the note, the `ref, _ =` sites will silently stop reporting and
# these go red. That is the failure they exist to catch: a repo 0-behind and clean
# against the remote it tracks, while another remote holds commits running nowhere
# (shantytown, 3 days, 4 dark commits, 3 of them fixes to the staleness detector
# itself).
#
# NOTE FOR WHOEVER EDITS THESE: I first wrote them against a `ref, note =` change
# of my own and they passed — then passed IDENTICALLY with that change reverted,
# because the note was already arriving by the other path. A test that cannot
# distinguish the fix from its absence is the bug this repo keeps re-finding. The
# converged case below is what makes these falsifiable; do not delete it.

def _two_remotes_second_ahead(tmp_path: Path) -> Path:
    """A shared checkout whose `main` tracks origin/main, plus a SECOND remote
    holding a commit origin does not have — the shanty fork, in miniature."""
    origin = tmp_path / "origin.git"
    forge = tmp_path / "forge.git"
    for bare in (origin, forge):
        bare.mkdir()
        _git(bare, "init", "-q", "--bare", "-b", "main")

    repo = _shared_repo(tmp_path, name="proj")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "remote", "add", "forge", str(forge))
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "push", "-q", "forge", "main")
    _git(repo, "branch", "--set-upstream-to=origin/main", "main")

    # one commit that reaches ONLY forge — dark to anyone tracking origin
    (repo / "dark.txt").write_text("a fix nobody is running\n")
    _git(repo, "add", "dark.txt")
    _git(repo, "commit", "-q", "-m", "fix: dark on forge only")
    _git(repo, "push", "-q", "forge", "main")
    _git(repo, "reset", "-q", "--hard", "HEAD~1")   # local returns to origin's tip
    _git(repo, "fetch", "-q", "--all")
    return repo


def _converge(repo: Path):
    """Make origin carry forge's commit too — the same fixture, no divergence."""
    _git(repo, "push", "-q", "origin", "refs/remotes/forge/main:refs/heads/main")
    _git(repo, "fetch", "-q", "--all")


def _dispatch(tmp_path, monkeypatch, repo):
    root = _root(tmp_path)
    panes = NullPanes(screen="")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: panes)
    rc = main(["--root", str(root), "go", "item-1", "ellie", "--worktree", str(repo)])
    return rc, panes


def test_dispatch_reports_a_second_remote_that_is_ahead(tmp_path, monkeypatch, capsys):
    repo = _two_remotes_second_ahead(tmp_path)
    rc, panes = _dispatch(tmp_path, monkeypatch, repo)
    assert rc == OK
    cap = capsys.readouterr()
    assert panes.sent, "nothing was dispatched — this test is not exercising the path"

    everywhere = cap.out + cap.err + "".join(t for _p, t in panes.sent)
    assert "forge/main" in everywhere, (
        "a second remote holding work this tree does not have was NOT reported; "
        f"got: {everywhere!r}")
    assert "ahead" in everywhere
    # and it reaches the AGENT, not just the operator's terminal
    assert "forge/main" in "".join(t for _p, t in panes.sent), \
        "the divergence stayed on the console and never rode the dispatch"


def test_dispatch_is_SILENT_when_the_two_remotes_agree(tmp_path, monkeypatch, capsys):
    """The falsifier. Without this, the assertion above passes for a build that
    shouts unconditionally — which is the same as one that never checked."""
    repo = _two_remotes_second_ahead(tmp_path)
    _converge(repo)
    rc, panes = _dispatch(tmp_path, monkeypatch, repo)
    assert rc == OK
    cap = capsys.readouterr()
    everywhere = cap.out + cap.err + "".join(t for _p, t in panes.sent)
    assert "ahead" not in everywhere, f"converged remotes still warned: {everywhere!r}"


def test_st_worktree_reports_a_second_remote_that_is_ahead(tmp_path, capsys):
    """`st worktree` is the command that prints the bare 'current with <ref>'
    line — the one a human reads as an all-clear before starting work."""
    repo = _two_remotes_second_ahead(tmp_path)
    root = _root(tmp_path)
    rc = main(["--root", str(root), "worktree", str(repo), "ellie"])
    assert rc == OK
    cap = capsys.readouterr()
    assert "forge/main" in cap.out + cap.err, \
        f"st worktree did not report the divergence: {cap.out + cap.err!r}"


def test_st_worktree_is_SILENT_when_the_two_remotes_agree(tmp_path, capsys):
    repo = _two_remotes_second_ahead(tmp_path)
    _converge(repo)
    root = _root(tmp_path)
    rc = main(["--root", str(root), "worktree", str(repo), "ellie"])
    assert rc == OK
    cap = capsys.readouterr()
    assert "ahead" not in cap.out + cap.err, \
        f"converged remotes still warned: {cap.out + cap.err!r}"
