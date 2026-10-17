"""`st repo push` — push to EVERY remote, refuse on non-ff, NAME the remote (aegis-96few).

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
    rc = main(["push", str(repo), "ellie", "--branch", "main"])
    assert rc == OK
    mine = _sha(wt, "HEAD")
    assert _sha(origin, "main") == mine, "origin did not receive the push"
    assert _sha(forge, "main") == mine, (
        "forge did not receive the push — pushing ONE remote is the fork")
    out = capsys.readouterr().out
    assert "origin" in out and "forge" in out, f"remotes not both reported: {out!r}"


@pytest.mark.parametrize("prefix", [["push"], ["repo", "push"]])
@pytest.mark.parametrize("destination", [[], ["--branch", ""]])
def test_destination_intent_required_before_remote_contact(
        two_remotes, monkeypatch, capsys, prefix, destination):
    from shantytown import cli
    repo, wt, origin, forge = two_remotes
    _commit(wt, "unpublished-feature")
    dirty = wt / "dirty.txt"
    dirty.write_text("preserve\n")
    before = [_sha(peer, "main") for peer in (origin, forge)]
    head = _sha(wt, "HEAD")
    # Source/worktree resolution itself must not be needed to reject intent.
    monkeypatch.setattr(cli, "_resolve_push_worktree",
                        lambda *a: pytest.fail("must refuse before resolution"))
    monkeypatch.setattr(cli, "push_every_remote",
                        lambda *a: pytest.fail("must not contact peers"))
    assert main([*prefix, str(repo), "ellie", *destination]) == REFUSED
    assert "explicit destination" in capsys.readouterr().err
    assert [_sha(peer, "main") for peer in (origin, forge)] == before
    assert _sha(wt, "HEAD") == head
    assert dirty.read_text() == "preserve\n"


def test_explicit_feature_destination_keeps_hooks_and_main_refs(two_remotes):
    repo, wt, origin, forge = two_remotes
    before = [_sha(peer, "main") for peer in (origin, forge)]
    _commit(wt, "feature-for-review")
    log = repo / "hook-log"
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text(f'#!/bin/sh\necho hook >> "{log}"\n')
    hook.chmod(0o755)
    assert main(["repo", "push", str(repo), "ellie", "--branch", "wt/ellie"]) == OK
    assert [_sha(peer, "main") for peer in (origin, forge)] == before
    assert all(_sha(peer, "wt/ellie") == _sha(wt, "HEAD") for peer in (origin, forge))
    assert log.read_text().splitlines() == ["hook", "hook"]


def test_checked_out_noncanonical_branch_REFUSES_before_contacting_remotes(
        two_remotes, monkeypatch, capsys):
    """aegis-72png: a dedicated branch must not publish parked canonical work.

    This is the measured production shape: the canonical wt/agent branch holds
    a deliberately deferred commit, while the caller stands in a second
    worktree on an unrelated fix branch. Both remote refs staying unchanged is
    the proof that the mismatch is discovered before any push or partial push.
    """
    repo, wt, origin, forge = two_remotes
    _commit(wt, "held-back")
    before_origin, before_forge = _sha(origin, "main"), _sha(forge, "main")

    dedicated = repo.parent / "dedicated"
    _git(repo, "worktree", "add", "-q", "-b", "fix/unrelated", str(dedicated),
         "main")
    _git(dedicated, "config", "user.email", "t@example.invalid")
    _git(dedicated, "config", "user.name", "t")
    _commit(dedicated, "unrelated-fix")
    monkeypatch.chdir(dedicated)

    assert main(["push", str(repo), "ellie", "--branch", "main"]) == REFUSED
    err = capsys.readouterr().err
    assert "refused before push" in err
    assert "fix/unrelated" in err and "wt/ellie" in err
    assert str(dedicated) in err and str(wt) in err
    assert "No remote was contacted" in err
    assert _sha(origin, "main") == before_origin
    assert _sha(forge, "main") == before_forge


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
    assert main(["push", str(repo), "ellie", "--branch", "main"]) == OK
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

    assert main(["push", str(repo), "ellie", "--branch", "main"]) == REFUSED
    err = capsys.readouterr().err
    assert "REFUSED BEFORE PUSH" in err
    assert "third-party" in err and "no remote was contacted" in err
    assert _sha(origin, "main") == before_origin
    assert _sha(forge, "main") == before_forge


def test_bare_name_finds_existing_workspace_worktree(
        two_remotes, monkeypatch, tmp_path, capsys):
    """aegis-ubss6: push follows the existing ~/workspace worktree.

    A nonexistent ~/gt checkout must not hide it or cause the refusal to suggest
    provisioning a duplicate under ~/gt.
    """
    repo, wt, origin, forge = two_remotes
    monkeypatch.setenv("GT_ROOT", str(tmp_path / "gt"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    _commit(wt, "workspace-work")

    assert main(["push", "proj", "ellie", "--branch", "main"]) == OK
    assert _sha(origin, "main") == _sha(wt, "HEAD")
    assert _sha(forge, "main") == _sha(wt, "HEAD")
    assert "no worktree" not in capsys.readouterr().err


def test_bare_name_refuses_ambiguous_existing_worktrees(
        two_remotes, monkeypatch, tmp_path, capsys):
    """Never guess when ~/gt and ~/workspace both carry the requested agent."""
    _, wt, _, _ = two_remotes
    gt_root = tmp_path / "gt"
    duplicate = gt_root / "proj-wt" / "ellie"
    duplicate.mkdir(parents=True)
    monkeypatch.setenv("GT_ROOT", str(gt_root))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))

    assert main(["push", "proj", "ellie", "--branch", "main"]) == REFUSED
    err = capsys.readouterr().err
    assert "ambiguous worktree" in err
    assert str(wt) in err and str(duplicate) in err


def test_workspace_resolution_keeps_third_party_remote_preflight(
        two_remotes, monkeypatch, tmp_path, capsys):
    """Finding a workspace worktree must not bypass the Thinker safety guard."""
    repo, wt, origin, forge = two_remotes
    monkeypatch.setenv("GT_ROOT", str(tmp_path / "gt"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    _git(repo, "config", "--unset", "remote.origin.st-push-allowed")
    _git(repo, "config", "--unset", "remote.forge.st-push-allowed")
    before_origin, before_forge = _sha(origin, "main"), _sha(forge, "main")
    _commit(wt, "workspace-must-not-land")

    assert main(["push", "proj", "ellie", "--branch", "main"]) == REFUSED
    err = capsys.readouterr().err
    assert "REFUSED BEFORE PUSH" in err
    assert "no remote was contacted" in err
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
    rc = main(["push", str(repo), "ellie", "--branch", "main"])
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
    assert main(["push", str(repo), "ellie", "--branch", "main"]) == OK
    assert _sha(forge, "main") == _sha(wt, "HEAD")


def test_pre_push_hook_refusal_surfaces_complete_stderr(two_remotes, capsys):
    """A scrub guard speaks on stderr; its complete refusal is the remedy.

    Git's porcelain ref report is stdout, so a test that stubs only the runner
    cannot prove the real subprocess boundary retains both streams. Use an
    actual pre-push hook in the fixture's shared hooks directory.
    """
    repo, wt, origin, forge = two_remotes
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text(
        "#!/bin/sh\n"
        "echo 'SCRUB REFUSED: internal identifier detected' >&2\n"
        "echo 'remove the .svc hostname before public push' >&2\n"
        "exit 1\n"
    )
    hook.chmod(0o755)
    _commit(wt, "must-be-scrubbed")

    assert main(["push", str(repo), "ellie", "--branch", "main"]) == REFUSED
    err = capsys.readouterr().err
    refusal = (
        "SCRUB REFUSED: internal identifier detected\n"
        "remove the .svc hostname before public push"
    )
    assert refusal in err, f"hook refusal was altered or truncated: {err!r}"
    assert "no output from git push" not in err
    assert _sha(origin, "main") != _sha(wt, "HEAD")
    assert _sha(forge, "main") != _sha(wt, "HEAD")


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
    assert main(["push", str(repo), "ellie", "--branch", "main"]) == REFUSED
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
    assert main(["push", str(repo), "ellie", "--branch", "main"]) == REFUSED
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


# --- requirement 3: never substitute a DIAGNOSIS for the mechanism's refusal --
#
# aegis-mmq38. Surfacing the hook's stderr (aegis-l3o0x) put guard prose into the
# same string the classifier read, and English is not a protocol: a scrub guard
# that writes "...or fetch first from the internal forge" made `st repo push` announce
# a non-fast-forward for BOTH remotes — one of which was a clean fast-forward —
# and prescribe a fetch-and-merge that cannot clear a content refusal at all. An
# operator who runs that loop learns the push tool is flaky, not that they just
# tried to publish an internal identifier; the next reach is `--no-verify`.
#
# Both arms, because a build that never says non-fast-forward would pass a
# one-directional version of this and be just as wrong.

def _refusing_hook(repo: Path, body: str) -> None:
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\n{body}\nexit 1\n")
    hook.chmod(0o755)


def test_hook_prose_quoting_git_is_NOT_diagnosed_as_non_fast_forward(
        two_remotes, capsys):
    repo, wt, origin, forge = two_remotes
    _refusing_hook(repo, (
        "echo 'REFUSED: this push would add internal identifiers to a PUBLIC "
        "remote.' >&2\n"
        "echo '  Scrub them and amend, or fetch first from the internal forge "
        "instead.' >&2"))
    _commit(wt, "carries-an-internal-identifier")

    assert main(["push", str(repo), "ellie", "--branch", "main"]) == REFUSED
    err = capsys.readouterr().err
    assert "internal identifiers" in err, (
        f"the guard's own refusal did not reach the operator: {err!r}")
    # The guard said "fetch first" as ENGLISH. Git said nothing of the kind, and
    # neither may we.
    assert "REFUSED: non-fast-forward" not in err, (
        f"hook prose was mistaken for git's verdict: {err!r}")
    assert "NOT a non-fast-forward" in err, (
        f"the operator was not told fetch-and-merge is the wrong remedy: {err!r}")
    # and nothing landed anywhere — the hook refused every remote
    assert _sha(origin, "main") != _sha(wt, "HEAD")
    assert _sha(forge, "main") != _sha(wt, "HEAD")


def test_a_real_non_fast_forward_is_STILL_diagnosed(two_remotes, capsys):
    """The counterpart. Classifying from git's porcelain stdout must not have
    cost us the diagnosis that was already right."""
    repo, wt, origin, forge = two_remotes
    other = repo.parent / "other-nff"
    _git(repo.parent, "clone", "-q", str(forge), str(other))
    _git(other, "config", "user.email", "t@example.invalid")
    _git(other, "config", "user.name", "t")
    (other / "theirs.txt").write_text("their work\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "their work")
    _git(other, "push", "-q", "origin", "main")

    _commit(wt, "mine")
    assert main(["push", str(repo), "ellie", "--branch", "main"]) == REFUSED
    err = capsys.readouterr().err
    assert "forge REFUSED: non-fast-forward" in err, (
        f"a genuine non-fast-forward lost its diagnosis: {err!r}")
    assert "fetch" in err.lower() and "merge" in err.lower()


def test_the_failure_names_what_it_actually_attempted(two_remotes, capsys):
    """Two instruments disagreeing is what made this bug cost hours: `st repo push`
    pushes `wt/<agent>` from the AGENT'S WORKTREE, while the operator's check was
    a bare `git push` in whatever tree they were standing in. Naming the ref and
    the directory makes st's claim reproducible with plain git."""
    repo, wt, origin, forge = two_remotes
    _refusing_hook(repo, "echo 'guard says no' >&2")
    _commit(wt, "whatever")

    assert main(["push", str(repo), "ellie", "--branch", "main"]) == REFUSED
    err = capsys.readouterr().err
    assert "wt/ellie" in err and "main" in err, (
        f"the failure did not say WHICH ref it pushed: {err!r}")
    assert str(wt) in err, (
        f"the failure did not say WHERE it pushed from: {err!r}")


def test_porcelain_classifier_reads_ref_status_lines_only():
    """Unit-level, both directions. `_push_rejected_non_ff` is the whole reason
    the streams are kept apart, so it is asserted directly."""
    from shantytown.workspace import _push_rejected_non_ff

    git_said_non_ff = ("To ../forge.git\n"
                       "!\trefs/heads/wt/ellie:refs/heads/main\t"
                       "[rejected] (fetch first)\nDone")
    assert _push_rejected_non_ff(git_said_non_ff) is True

    # A hook decline: git never reached the ref update, so porcelain STDOUT is
    # empty. It has no opinion, and neither may we.
    assert _push_rejected_non_ff("") is False

    # Prose that quotes git's wording, on any stream, is not a ref-status line.
    assert _push_rejected_non_ff(
        "REFUSED: scrub the identifier, or fetch first from the forge") is False
    assert _push_rejected_non_ff("! [rejected] fetch first") is False, (
        "a bare bang line without the tab-separated ref-status shape is prose")


# --- requirement: push the branch the WORKTREE IS ON, never a constructed name ---
#
# aegis-8ijj33. `branch` is built as wt/<agent> and nothing checked the worktree was
# on it. _push_invocation_branch covers a caller standing in ANOTHER worktree of the
# repo and deliberately ignores a caller elsewhere — which is the common case for a
# lead pushing on someone's behalf, and so the uncovered one.
#
# MEASURED 2026-09-20: `st repo push shantytown wu` from a crew clone pushed `wt/wu`
# -> main while the worktree was on `wu/nyce0l-declared-roles` and wt/wu was STALE.
# It published neither the tree's work nor anything the caller had seen, and only a
# non-fast-forward rejection stopped it. Had wt/wu been an ancestor of main it would
# have landed silently, and on a CD-wired repo that is a deploy.

def test_a_worktree_on_ANOTHER_branch_is_REFUSED_and_no_remote_moves(two_remotes, capsys):
    repo, wt, origin, forge = two_remotes
    _commit(wt, "work")
    before_origin, before_forge = _sha(origin, "main"), _sha(forge, "main")
    # The agent moved the worktree to a topic branch; wt/ellie is now stale.
    _git(wt, "checkout", "-q", "-b", "ellie/topic")
    _commit(wt, "work on the topic branch")

    rc = main(["push", str(repo), "ellie", "--branch", "main"])

    assert rc == REFUSED, "pushed a branch the worktree is not on"
    out = capsys.readouterr()
    msg = out.out + out.err
    assert "ellie/topic" in msg, f"refusal does not name what is checked out: {msg!r}"
    assert "wt/ellie" in msg, f"refusal does not name what it would have pushed: {msg!r}"
    assert _sha(origin, "main") == before_origin, "origin moved despite the refusal"
    assert _sha(forge, "main") == before_forge, "forge moved despite the refusal"


def test_the_canonical_case_is_UNTOUCHED_by_that_guard(two_remotes):
    """The control. Without it the guard could pass by refusing everything."""
    repo, wt, origin, forge = two_remotes
    _commit(wt, "work")
    assert main(["push", str(repo), "ellie", "--branch", "main"]) == OK
    assert _sha(origin, "main") == _sha(wt, "HEAD")
    assert _sha(forge, "main") == _sha(wt, "HEAD")


def test_worktree_unrelated_refuses_before_refresh_and_explicit_resume(
        two_remotes, monkeypatch, capsys):
    from shantytown import cli
    repo, wt, origin, forge = two_remotes
    _git(wt, 'checkout', '-b', 'fix/held')
    (wt / 'uncommitted').write_text('keep')
    refreshed = []
    monkeypatch.setattr(cli, '_refresh_worktree', lambda path: refreshed.append(path))
    assert main(['repo', 'worktree', str(repo), 'ellie']) == REFUSED
    assert refreshed == []
    assert 'fix/held' in capsys.readouterr().err
    assert main(['repo', 'worktree', str(repo), 'ellie', '--resume']) == OK
    assert len(refreshed) == 1
    assert 'RESUMING' in capsys.readouterr().out
    assert _git(wt, 'branch', '--show-current') == 'fix/held'
    assert (wt / 'uncommitted').read_text() == 'keep'


def test_explicit_source_branch_publishes_exact_topic_to_both_peers(two_remotes):
    repo, wt, origin, forge = two_remotes
    _git(wt, 'checkout', '-b', 'fix/held')
    _commit(wt, 'topic-work')
    assert main(['repo', 'push', str(repo), 'ellie', '--source-branch',
                 'fix/held', '--branch', 'fix/held']) == OK
    for remote in (origin, forge):
        assert _sha(remote, 'fix/held') == _sha(wt, 'HEAD')
        assert _sha(remote, 'main') != _sha(wt, 'HEAD')


def test_explicit_extra_worktree_publishes_only_its_source_and_preserves_canonical(
        two_remotes, monkeypatch):
    repo, canonical, origin, forge = two_remotes
    _git(canonical, 'checkout', '-b', 'ellie/old-held')
    (canonical / 'uncommitted').write_text('preserve canonical')
    before = (_sha(canonical, 'HEAD'), _git(canonical, 'status', '--porcelain'),
              (repo / '.git' / 'config').read_bytes(),
              (repo / '.git' / 'worktrees' / 'ellie' / 'index').read_bytes())
    extra = canonical.parent / 'ellie-topic'
    _git(repo, 'worktree', 'add', '-b', 'ellie/topic', str(extra), 'main')
    _commit(extra, 'extra-topic')
    monkeypatch.chdir(extra)
    assert main(['repo', 'push', str(extra), 'ellie', '--source-branch',
                 'ellie/topic', '--branch', 'ellie/topic']) == OK
    assert all(_sha(peer, 'ellie/topic') == _sha(extra, 'HEAD') for peer in (origin, forge))
    assert all(_sha(peer, 'main') == before[0] for peer in (origin, forge))
    after = (_sha(canonical, 'HEAD'), _git(canonical, 'status', '--porcelain'),
             (repo / '.git' / 'config').read_bytes(),
             (repo / '.git' / 'worktrees' / 'ellie' / 'index').read_bytes())
    assert after == before
    assert _git(canonical, 'branch', '--show-current') == 'ellie/old-held'


@pytest.mark.parametrize('caller,source', [('canonical', 'ellie/topic'),
                                         ('extra', 'wt/ellie')])
def test_extra_worktree_retains_caller_and_source_branch_refusals(
        two_remotes, monkeypatch, caller, source):
    from shantytown import cli
    repo, canonical, origin, forge = two_remotes
    extra = canonical.parent / 'ellie-topic'
    _git(repo, 'worktree', 'add', '-b', 'ellie/topic', str(extra), 'main')
    monkeypatch.chdir(canonical if caller == 'canonical' else extra)
    monkeypatch.setattr(cli, 'push_every_remote', lambda *a: pytest.fail('must not contact any peer'))
    assert main(['repo', 'push', str(extra), 'ellie', '--source-branch', source,
                 '--branch', 'ellie/topic']) == REFUSED
    assert all(_sha(peer, 'main') == _sha(canonical, 'HEAD') for peer in (origin, forge))


def test_extra_worktree_keeps_all_remote_trust_preflight(two_remotes, monkeypatch):
    repo, canonical, origin, forge = two_remotes
    extra = canonical.parent / 'ellie-topic'
    _git(repo, 'worktree', 'add', '-b', 'ellie/topic', str(extra), 'main')
    _commit(extra, 'held-topic')
    _git(repo, 'config', 'remote.forge.st-push-allowed', 'false')
    monkeypatch.chdir(extra)
    assert main(['repo', 'push', str(extra), 'ellie', '--source-branch',
                 'ellie/topic', '--branch', 'ellie/topic']) == REFUSED
    for peer in (origin, forge):
        assert _sha(peer, 'main') == _sha(canonical, 'HEAD')
        assert subprocess.run(['git', '-C', str(peer), 'rev-parse', '--verify',
                               'refs/heads/ellie/topic'], capture_output=True).returncode != 0


@pytest.mark.parametrize('marker', ['malformed', 'copied'])
def test_explicit_gitfile_cannot_alias_an_unregistered_root(two_remotes, marker):
    from shantytown import cli
    from shantytown.workspace import WorkspaceError
    repo, canonical, _, _ = two_remotes
    impostor = canonical.parent / 'ellie-impostor'
    impostor.mkdir()
    (impostor / '.git').write_text('not a gitdir' if marker == 'malformed'
                                 else (canonical / '.git').read_text())
    with pytest.raises(WorkspaceError, match='linked worktree identity'):
        cli._resolve_push_worktree(str(impostor), 'ellie')


def test_explicit_wrong_source_refuses_without_remote_write(two_remotes):
    repo, wt, origin, forge = two_remotes
    before = [_sha(remote, 'main') for remote in (origin, forge)]
    assert main(['repo', 'push', str(repo), 'ellie', '--source-branch',
                 'fix/not-checked-out', '--branch', 'main']) == REFUSED
    assert [_sha(remote, 'main') for remote in (origin, forge)] == before


def test_explicit_source_requires_readable_branch(two_remotes, monkeypatch):
    from shantytown import cli
    repo, wt, origin, forge = two_remotes
    monkeypatch.setattr(cli, '_worktree_branch', lambda path: None)
    monkeypatch.setattr(cli, 'push_every_remote', lambda *a: pytest.fail('must not push'))
    assert main(['repo', 'push', str(repo), 'ellie', '--source-branch',
                 'wt/ellie', '--branch', 'main']) == REFUSED


def test_explicit_empty_source_with_detached_tree_never_publishes_stale_canonical(
        two_remotes):
    repo, wt, origin, forge = two_remotes
    canonical = _sha(wt, 'HEAD')
    _git(wt, 'checkout', '--detach')
    _commit(wt, 'held-detached-work')
    assert _sha(wt, 'HEAD') != canonical
    assert main(['repo', 'push', str(repo), 'ellie', '--source-branch', '',
                 '--branch', 'empty-source-probe']) == REFUSED
    for remote in (origin, forge):
        result = subprocess.run(['git', '-C', str(remote), 'rev-parse', '--verify',
                                 'refs/heads/empty-source-probe'],
                                capture_output=True, text=True)
        assert result.returncode != 0
        assert _sha(remote, 'main') == canonical
    assert _sha(wt, 'wt/ellie') == canonical
    assert _sha(wt, 'HEAD') != canonical


def test_explicit_timeout_allows_real_slow_hook(two_remotes):
    repo, wt, origin, forge = two_remotes
    _commit(wt, "slow-hook-feature")
    hooks = Path(_git(wt, "rev-parse", "--git-path", "hooks"))
    if not hooks.is_absolute():
        hooks = wt / hooks
    hook = hooks / "pre-push"
    hook.write_text("#!/bin/sh\nsleep 61\nexit 0\n")
    hook.chmod(0o755)
    assert main(["repo", "push", str(repo), "ellie", "--branch", "slow-feature",
                 "--timeout", "90"]) == OK
    assert _sha(origin, "slow-feature") == _sha(wt, "HEAD")
    assert _sha(forge, "slow-feature") == _sha(wt, "HEAD")


@pytest.mark.parametrize("timeout", ["0", "-1", "3601"])
def test_invalid_push_timeout_has_no_remote_write(two_remotes, timeout):
    repo, wt, origin, forge = two_remotes
    _commit(wt, "must-stay-local")
    before = [_sha(r, "main") for r in (origin, forge)]
    assert main(["repo", "push", str(repo), "ellie", "--branch", "main",
                 "--timeout", timeout]) == REFUSED
    assert [_sha(r, "main") for r in (origin, forge)] == before


def test_timeout_is_indeterminate_and_stops_other_peers(two_remotes):
    repo, wt, origin, forge = two_remotes
    calls = []
    def timed_out(dest, *args):
        calls.append(args)
        return 124, "", "read back destination SHA before retry"
    result = push_every_remote(wt, "wt/ellie", "feature", push_run=timed_out)
    assert len(calls) == len(result) == 1
    assert not result[0].ok and not result[0].non_ff
    assert "INDETERMINATE" in result[0].reason
    assert "read back" in result[0].reason


def test_real_hook_timeout_keeps_destination_absent(two_remotes):
    from shantytown.workspace import _git_push
    repo, wt, origin, forge = two_remotes
    _commit(wt, "blocked-by-hook")
    hooks = Path(_git(wt, "rev-parse", "--git-path", "hooks"))
    if not hooks.is_absolute():
        hooks = wt / hooks
    hook = hooks / "pre-push"
    hook.write_text("#!/bin/sh\nsleep 5\nexit 0\n")
    hook.chmod(0o755)
    rc, stdout, stderr = _git_push(wt, "--porcelain", "origin",
                                  "wt/ellie:refs/heads/feature", timeout=1)
    assert rc == 124 and stdout == "" and "INDETERMINATE" in stderr
    result = subprocess.run(["git", "-C", str(origin), "show-ref", "--verify",
                             "refs/heads/feature"], capture_output=True)
    assert result.returncode != 0


def test_timeout_after_remote_commit_is_still_indeterminate(two_remotes):
    repo, wt, origin, forge = two_remotes
    _commit(wt, "committed-before-lost-response")
    calls = []
    def committed_timeout(dest, *args):
        calls.append(args)
        _git(dest, "push", *args)
        return 124, "", "read back destination SHA before retry"
    result = push_every_remote(wt, "wt/ellie", "feature", push_run=committed_timeout)
    assert len(calls) == len(result) == 1
    assert not result[0].ok and "INDETERMINATE" in result[0].reason
    # Read-back proves the first sorted peer committed despite the timeout.
    assert _sha(forge, "feature") == _sha(wt, "HEAD")
    missing = subprocess.run(["git", "-C", str(origin), "show-ref", "--verify",
                              "refs/heads/feature"], capture_output=True)
    assert missing.returncode != 0


def test_partial_timeout_does_not_claim_remote_rejection(two_remotes, monkeypatch, capsys):
    from shantytown import cli
    from shantytown.workspace import PushOutcome
    repo, wt, origin, forge = two_remotes
    monkeypatch.setattr(cli, "push_every_remote", lambda *args: [
        PushOutcome(remote="forge", ok=True),
        PushOutcome(remote="origin", ok=False, reason="origin INDETERMINATE")])
    assert main(["repo", "push", str(repo), "ellie", "--branch", "feature"]) == REFUSED
    err = capsys.readouterr().err
    assert "confirmed publication" in err and "Read back" in err
    assert "did not" not in err


def test_selected_origin_only_leaves_other_peer_unchanged(two_remotes):
    repo, wt, origin, forge = two_remotes
    _commit(wt, 'selected-feature')
    assert main(['repo', 'push', str(repo), 'ellie', '--remote', 'origin',
                 '--branch', 'feature']) == OK
    assert _sha(origin, 'feature') == _sha(wt, 'HEAD')
    assert not _git(forge, 'for-each-ref', '--format=%(refname)', 'refs/heads/feature')


def test_unknown_selected_remote_never_pushes(two_remotes):
    _, wt, *_ = two_remotes
    result = push_every_remote(wt, 'HEAD', 'feature', selected_remotes=('typo',),
                              push_run=lambda *a: pytest.fail('must not push'))
    assert len(result) == 1 and not result[0].ok
    assert 'unconfigured' in result[0].reason


@pytest.mark.parametrize('reason', ['maintenance', ''])
def test_frozen_set_refuses_before_any_peer_and_preserves_work(two_remotes, reason):
    repo, wt, origin, forge = two_remotes
    _commit(wt, 'held-feature')
    dirty = wt / 'keep.txt'; dirty.write_text('unsaved')
    _git(wt, 'config', 'st.push-freeze.local', reason)
    result = push_every_remote(wt, 'HEAD', 'feature',
                              push_run=lambda *a: pytest.fail('must not push'))
    assert len(result) == 1 and not result[0].ok
    assert 'write-frozen' in result[0].reason
    assert all(not _git(peer, 'for-each-ref', '--format=%(refname)', 'refs/heads/feature')
               for peer in (origin, forge))
    assert dirty.read_text() == 'unsaved'


def test_freeze_checks_all_effective_push_urls_before_any_push(two_remotes):
    _, wt, *_ = two_remotes
    _git(wt, 'remote', 'set-url', '--push', 'origin', 'https://allowed.example/team/repo')
    _git(wt, 'remote', 'set-url', '--add', '--push', 'origin', 'https://frozen.example/team/repo')
    _git(wt, 'config', 'st.push-freeze.frozen.example', 'maintenance')
    result = push_every_remote(wt, 'HEAD', 'feature', selected_remotes=('origin',),
                              push_run=lambda *a: pytest.fail('must not push'))
    assert not result[0].ok and 'frozen.example' in result[0].reason


def test_freeze_checks_push_instead_of_rewrite(two_remotes):
    _, wt, *_ = two_remotes
    _git(wt, 'remote', 'set-url', 'origin', 'fixture:team/repo')
    _git(wt, 'config', 'url.https://frozen.example/.pushInsteadOf', 'fixture:')
    _git(wt, 'config', 'st.push-freeze.frozen.example', 'maintenance')
    result = push_every_remote(wt, 'HEAD', 'feature', selected_remotes=('origin',),
                              push_run=lambda *a: pytest.fail('must not push'))
    assert not result[0].ok and 'frozen.example' in result[0].reason


def test_unreadable_freeze_config_refuses(two_remotes):
    from shantytown import workspace
    _, wt, *_ = two_remotes
    def run(dest, *args):
        if args[:2] == ('config', '--get-all'):
            return 128, ''
        return workspace._git(dest, *args)
    result = push_every_remote(wt, 'HEAD', 'feature', run=run,
                              push_run=lambda *a: pytest.fail('must not push'))
    assert not result[0].ok and 'freeze state' in result[0].reason


def test_unselected_frozen_host_does_not_block_selected_peer(two_remotes):
    _, wt, origin, forge = two_remotes
    _git(wt, 'remote', 'set-url', '--push', 'forge', 'https://frozen.example/team/repo')
    _git(wt, 'config', 'st.push-freeze.frozen.example', 'maintenance')
    result = push_every_remote(wt, 'HEAD', 'feature', selected_remotes=('origin',))
    assert len(result) == 1 and result[0].ok and result[0].remote == 'origin'
    assert _sha(origin, 'feature') == _sha(wt, 'HEAD')
    assert not _git(forge, 'for-each-ref', '--format=%(refname)', 'refs/heads/feature')


def test_global_hold_cannot_be_erased_by_local_empty_value(two_remotes, tmp_path, monkeypatch):
    _, wt, *_ = two_remotes
    global_config = tmp_path / 'global.gitconfig'
    global_config.write_text('[st "push-freeze"]\nlocal = maintenance\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(global_config))
    _git(wt, 'config', 'st.push-freeze.local', '')
    result = push_every_remote(wt, 'HEAD', 'feature',
                              push_run=lambda *a: pytest.fail('must not push'))
    assert not result[0].ok and 'write-frozen' in result[0].reason


def test_new_hold_after_first_publication_stops_remaining_peer(two_remotes):
    from shantytown import workspace
    _, wt, origin, forge = two_remotes
    def publish_then_hold(dest, *args):
        result = workspace._git_push(dest, *args)
        _git(dest, 'config', 'st.push-freeze.local', 'new freeze')
        return result
    outcomes = push_every_remote(wt, 'HEAD', 'feature', push_run=publish_then_hold)
    assert outcomes[0].remote == 'forge' and outcomes[0].ok
    assert outcomes[1].remote == 'origin' and not outcomes[1].ok
    assert _sha(forge, 'feature') == _sha(wt, 'HEAD')
    assert not _git(origin, 'for-each-ref', '--format=%(refname)', 'refs/heads/feature')


def test_host_with_unrepresentable_freeze_key_refuses(two_remotes):
    _, wt, *_ = two_remotes
    _git(wt, 'remote', 'set-url', '--push', 'origin', 'ssh://user@[::1]/team/repo')
    outcomes = push_every_remote(wt, 'HEAD', 'feature', selected_remotes=('origin',),
                                push_run=lambda *a: pytest.fail('must not push'))
    assert not outcomes[0].ok and 'cannot be checked' in outcomes[0].reason
