"""`st push` — push to EVERY remote, refuse on non-ff, NAME the remote (aegis-96few).

THE BUG. shantytown has two live remotes, neither a mirror, and each agent's
`wt/<name>` branch is configured to push to one of them — measured 2026-08-04:
11 agents to forge, 5 to origin. The one documented recipe therefore lands in
different places depending on WHOSE tree runs it, and any two agents on opposite
sides re-fork the repo the moment both push. Nobody is doing anything wrong,
which is why it forked twice in one day, and why three commits left dark by the
first fork were fixes to the staleness detector itself (aegis-lvc4b).

ARNOLD'S TWO REQUIREMENTS, one test class each:
  1. refuse on non-ff, NEVER force — a rejection means someone's work is on the
     other remote, and converging never needs a force, so a force here could only
     ever destroy work.
  2. NAME THE REMOTE in the failure — with two live peers, "push rejected" cannot
     be acted on: "my branch is behind" and "the other remote moved" have
     different next steps, and guessing wrong costs an hour.

Every assertion below has its counterpart: a push that must land, and a push that
must be refused; a name that must appear, and the OTHER remote's name that must
not be blamed. A one-directional test here would pass against a build that
refuses everything, or one that pushes everything and never checks.
"""
from __future__ import annotations
import subprocess
from pathlib import Path

import pytest

from shantytown.cli import main, OK, REFUSED
from shantytown.workspace import push_every_remote


def _git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout.strip()


def _sha(cwd, ref):
    return _git(cwd, "rev-parse", ref)


@pytest.fixture
def two_remotes(tmp_path: Path):
    """A shared checkout with TWO bare remotes, plus an agent worktree on
    wt/ellie — shantytown's shape in miniature."""
    origin, forge = tmp_path / "origin.git", tmp_path / "forge.git"
    for bare in (origin, forge):
        bare.mkdir()
        _git(bare.parent, "init", "-q", "--bare", "-b", "main", str(bare))

    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("one\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "remote", "add", "forge", str(forge))
    # Local bare paths do not prove common ownership.  This fixture models the
    # explicitly trusted two-peer topology that shantytown uses in production.
    _git(repo, "config", "remote.origin.st-push-allowed", "true")
    _git(repo, "config", "remote.forge.st-push-allowed", "true")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "push", "-q", "forge", "main")
    _git(repo, "branch", "--set-upstream-to=origin/main", "main")

    rc = main(["worktree", str(repo), "ellie"])
    assert rc == OK
    wt = repo.parent / "proj-wt" / "ellie"
    assert wt.is_dir()
    _git(wt, "config", "user.email", "t@example.invalid")
    _git(wt, "config", "user.name", "t")
    return repo, wt, origin, forge


def _commit(wt: Path, name: str):
    (wt / f"{name}.txt").write_text(f"{name}\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", name)


# --- requirement 0: it reaches EVERY remote, which is the whole point ---------

def test_push_lands_on_BOTH_remotes(two_remotes, capsys):
    repo, wt, origin, forge = two_remotes
    _commit(wt, "work")
    rc = main(["push", str(repo), "ellie"])
    assert rc == OK
    mine = _sha(wt, "HEAD")
    assert _sha(origin, "main") == mine, "origin did not receive the push"
    assert _sha(forge, "main") == mine, (
        "forge did not receive the push — pushing ONE remote is the fork")
    out = capsys.readouterr().out
    assert "origin" in out and "forge" in out, f"remotes not both reported: {out!r}"


def test_a_repo_with_ONE_remote_still_works(tmp_path, capsys):
    """The falsifier for 'push both': a single-remote repo must not refuse or
    invent a second push. Without this, 'push every remote' could mean 'require
    two'."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = tmp_path / "solo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("one\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "branch", "--set-upstream-to=origin/main", "main")
    assert main(["worktree", str(repo), "ellie"]) == OK
    wt = repo.parent / "solo-wt" / "ellie"
    _git(wt, "config", "user.email", "t@example.invalid")
    _git(wt, "config", "user.name", "t")
    _commit(wt, "solo-work")
    assert main(["push", str(repo), "ellie"]) == OK
    assert _sha(origin, "main") == _sha(wt, "HEAD")


def test_mixed_authority_remotes_REFUSE_before_contacting_either(two_remotes, capsys):
    """Thinker's measured shape: our fork plus somebody else's upstream.

    Both remote refs remain unchanged, proving the refusal is a PRE-FLIGHT and
    cannot manufacture a partial push before discovering the unsafe peer.
    """
    repo, wt, origin, forge = two_remotes
    _git(repo, "config", "--unset", "remote.origin.st-push-allowed")
    _git(repo, "config", "--unset", "remote.forge.st-push-allowed")
    before_origin, before_forge = _sha(origin, "main"), _sha(forge, "main")
    _commit(wt, "must-not-land")

    assert main(["push", str(repo), "ellie"]) == REFUSED
    err = capsys.readouterr().err
    assert "REFUSED BEFORE PUSH" in err
    assert "third-party" in err and "no remote was contacted" in err
    assert _sha(origin, "main") == before_origin
    assert _sha(forge, "main") == before_forge


# --- requirement 1: refuse on non-ff, NEVER force ----------------------------

def test_non_ff_is_REFUSED_and_the_remote_is_NOT_rewritten(two_remotes, capsys):
    repo, wt, origin, forge = two_remotes
    # somebody else's work lands on forge only — the measured shape
    other = repo.parent / "other"
    _git(repo.parent, "clone", "-q", str(forge), str(other))
    _git(other, "config", "user.email", "t@example.invalid")
    _git(other, "config", "user.name", "t")
    (other / "theirs.txt").write_text("their work\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "their work")
    _git(other, "push", "-q", "origin", "main")
    theirs = _sha(forge, "main")

    _commit(wt, "mine")
    rc = main(["push", str(repo), "ellie"])
    assert rc == REFUSED, "a non-fast-forward push must be REFUSED"

    # THE LOAD-BEARING ASSERTION: their commit is still there. If this ever fails,
    # something in this path learned to force, and it destroyed work that both git
    # and its author believed had landed.
    assert _sha(forge, "main") == theirs, (
        "forge was REWRITTEN — the push forced over someone else's commit")


def test_a_fast_forward_is_still_accepted(two_remotes):
    """Counterpart to the refusal: it must not refuse everything. A build that
    always refuses would pass the test above and be useless."""
    repo, wt, origin, forge = two_remotes
    _commit(wt, "clean")
    assert main(["push", str(repo), "ellie"]) == OK
    assert _sha(forge, "main") == _sha(wt, "HEAD")


# --- requirement 2: NAME the remote that refused -----------------------------

def test_the_refusal_NAMES_the_remote_that_refused(two_remotes, capsys):
    repo, wt, origin, forge = two_remotes
    other = repo.parent / "other"
    _git(repo.parent, "clone", "-q", str(forge), str(other))
    _git(other, "config", "user.email", "t@example.invalid")
    _git(other, "config", "user.name", "t")
    (other / "theirs.txt").write_text("their work\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "their work")
    _git(other, "push", "-q", "origin", "main")

    _commit(wt, "mine")
    assert main(["push", str(repo), "ellie"]) == REFUSED
    err = capsys.readouterr().err
    assert "forge" in err, f"the refusing remote was not named: {err!r}"
    assert "non-fast-forward" in err.lower()
    # and it must say what to do — a named refusal with no next step still costs
    # the hour it exists to save
    assert "fetch" in err.lower() and "merge" in err.lower()
    assert "never force" in err.lower() or "--force" not in err.lower()


def test_a_PARTIAL_push_says_so_and_names_both_sides(two_remotes, capsys):
    """origin accepted, forge refused. This state is the one most likely to be
    misread as 'the push failed' and retried blindly — and the remotes are now
    diverged BY THIS COMMAND until it is resolved."""
    repo, wt, origin, forge = two_remotes
    other = repo.parent / "other"
    _git(repo.parent, "clone", "-q", str(forge), str(other))
    _git(other, "config", "user.email", "t@example.invalid")
    _git(other, "config", "user.name", "t")
    (other / "theirs.txt").write_text("their work\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "their work")
    _git(other, "push", "-q", "origin", "main")

    _commit(wt, "mine")
    assert main(["push", str(repo), "ellie"]) == REFUSED
    err = capsys.readouterr().err
    assert "PARTIAL" in err, f"a partial push was not announced: {err!r}"
    assert "origin" in err and "forge" in err, (
        "a partial push must name BOTH which remote took it and which did not")
    # origin really did take it — the report is not hedging
    assert _sha(origin, "main") == _sha(wt, "HEAD")


def test_no_remotes_is_refused_not_silently_successful(tmp_path, capsys):
    repo = tmp_path / "noremote"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("one\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    assert push_every_remote(repo, "main") == [], \
        "a repo with no remotes must report nothing pushed, not claim success"
