"""The shared-checkout guard: is the worktree protocol ENFORCED, not just assisted?

THE TEST THAT MATTERS IS THE INERT ONE. A guard installed under `core.hooksPath`
is present, greppable, and structurally unable to fire — the install prints
success and a naive check says "installed". Every other case here is ordinary;
that one is a control reporting itself healthy while doing nothing, which is worse
than no control because it is believed.

Fixtures are REAL git repos (init + worktree add), not mocks, for the same reason
the shell installer grew a --selftest: the whole mechanism is "is `--absolute-git-dir`
the same path as `--git-common-dir`", and a mock that answers that question is a
mock of the answer. These run in ~2s total.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from shantytown import guard


def _git(cwd, *args, **kw):
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, **kw)


@pytest.fixture
def shared(tmp_path) -> Path:
    """A real shared checkout with a real linked worktree hanging off it."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "worktree", "add", "-q", "-b", "wt/tester",
         str(tmp_path / "proj-wt" / "tester"), "main")
    return repo


def _commit(cwd, msg, **env):
    import os
    e = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
             GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t", **env)
    (Path(cwd) / f"{abs(hash(msg)) % 10**8}.txt").write_text(msg)
    subprocess.run(["git", "-C", str(cwd), "add", "-A"], capture_output=True)
    return subprocess.run(["git", "-C", str(cwd), "commit", "-qm", msg],
                          capture_output=True, text=True, env=e)


# --- the verdicts -------------------------------------------------------------

def test_an_unguarded_repo_reports_MISSING(shared):
    c = guard.inspect(shared)
    assert c.state == guard.MISSING
    assert "unguarded" in c.why


def test_installing_makes_it_OK_and_is_idempotent_and_SILENT(shared):
    changed, note = guard.install(shared)
    assert changed and "guard installed" in note
    assert guard.inspect(shared).state == guard.OK
    # Re-running says NOTHING. A provisioning command that narrates a no-op every
    # time trains people to stop reading it.
    again, note2 = guard.install(shared)
    assert again is False and note2 == ""


def test_a_repo_that_is_not_a_checkout_is_its_own_verdict(tmp_path):
    c = guard.inspect(tmp_path / "ghost")
    assert c.state == guard.NO_CHECKOUT
    assert c.state != guard.MISSING, (
        "a container with no repo behind it is not an unguarded repo — "
        "installing is not the fix and the report must not imply it is")


# --- INERT: the case where a green install lies -------------------------------

def _set_hooks_path(repo: Path, rel: str) -> Path:
    d = repo / rel
    d.mkdir(parents=True, exist_ok=True)
    _git(repo, "config", "core.hooksPath", rel)
    return d


def test_core_hooksPath_makes_an_INSTALLED_guard_INERT(shared):
    """The whole point. Install first, THEN redirect: the hooks are genuinely on
    disk and genuinely dead, which is the state a naive check calls 'installed'."""
    guard.install(shared)
    assert guard.inspect(shared).state == guard.OK
    _set_hooks_path(shared, "scripts/hooks")

    c = guard.inspect(shared)
    assert c.state == guard.INERT
    assert c.state != guard.OK, "an unrunnable guard reported as covered"
    assert c.state != guard.MISSING, (
        "INERT rounded to MISSING understates it — somebody already installed "
        "it and the install did nothing")
    assert "CANNOT RUN" in c.why and "CHAINING" in c.why
    assert c.hooks_path and "scripts/hooks" in c.hooks_path


def test_INERT_is_proven_by_BEHAVIOUR_not_only_by_config(shared):
    """The verdict has to correspond to a commit actually going through. Without
    this the test suite would be asserting our own opinion of core.hooksPath."""
    guard.install(shared)
    assert _commit(shared, "blocked").returncode != 0, "guard did not guard"
    _set_hooks_path(shared, "scripts/hooks")
    assert _commit(shared, "now allowed").returncode == 0, (
        "core.hooksPath did not actually bypass the hook — then INERT would be "
        "the wrong verdict and this whole check is theatre")


def test_CHAINING_from_the_hooksPath_dir_restores_OK(shared):
    """The sanctioned fix, and it must be provable by the same call that reported
    the problem — 'chained' verified by --check, never by an install printing
    success."""
    guard.install(shared)
    hooks = _set_hooks_path(shared, "scripts/hooks")
    assert guard.inspect(shared).state == guard.INERT

    chained = hooks / "pre-commit"
    chained.write_text(
        "#!/bin/sh\n"
        f"# chains the {guard.MARKER}\n"
        f'exec "$(git rev-parse --git-common-dir)/hooks/pre-commit" "$@"\n')
    chained.chmod(0o755)

    c = guard.inspect(shared)
    assert c.state == guard.OK
    assert "CHAINED" in c.why
    assert _commit(shared, "still blocked").returncode != 0, (
        "reported OK-because-chained while the commit sailed through")


def test_install_REFUSES_on_INERT_rather_than_relocating(shared):
    """Writing into the hooksPath dir would clobber the hook that is the whole
    reason hooksPath was set — trading one silent breakage for another."""
    hooks = _set_hooks_path(shared, "scripts/hooks")
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
    (hooks / "pre-commit").chmod(0o755)
    before = (hooks / "pre-commit").read_text()

    with pytest.raises(guard.GuardError) as e:
        guard.install(shared)
    assert "CANNOT RUN" in str(e.value) and "Chain it" in str(e.value)
    assert (hooks / "pre-commit").read_text() == before, "clobbered a foreign hook"


# --- the guard's actual behaviour ---------------------------------------------

def test_it_blocks_the_shared_checkout_and_ALLOWS_the_worktree(shared, tmp_path):
    """Both outcomes. A guard that only ever says yes is a light, not a guard —
    and one that says no everywhere is a brick that will simply be uninstalled."""
    guard.install(shared)
    assert _commit(shared, "in shared").returncode != 0
    wt = tmp_path / "proj-wt" / "tester"
    r = _commit(wt, "in worktree")
    assert r.returncode == 0, f"guard bricked the worktree: {r.stderr[:200]}"


def test_the_override_works_and_is_the_documented_one(shared):
    """Maintenance has to remain possible, or the guard gets removed wholesale."""
    guard.install(shared)
    assert _commit(shared, "override me",
                   **{guard.DEFAULT_OVERRIDE_ENV: "1"}).returncode == 0


def test_the_refusal_names_the_worktree_remedy(shared):
    guard.install(shared)
    err = _commit(shared, "blocked").stderr
    assert "st worktree" in err, "a refusal with no remedy is a dead end"
    assert "NEVER force" in err
    assert guard.DEFAULT_OVERRIDE_ENV in err, (
        "the refusal must name its own escape hatch, or the only way past it is "
        "to uninstall the guard")
    assert "BLOCKED" in err and "SHARED checkout" in err


def test_it_does_not_replace_a_guard_it_did_not_write(shared):
    """A deployment shipping its own installer and st would otherwise overwrite
    each other on alternate runs — and their override variables differ, so an
    agent following the deployment's documented bypass would be blocked by st's
    copy. Recognition is by the shared MARKER."""
    gitdir = guard.common_git_dir(shared)
    hooks = gitdir / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    foreign = f"#!/bin/sh\n# someone else's {guard.MARKER}\nexit 7\n"
    (hooks / "pre-commit").write_text(foreign)
    (hooks / "pre-commit").chmod(0o755)

    assert guard.inspect(shared).state == guard.OK
    changed, _ = guard.install(shared)
    assert changed is False
    assert (hooks / "pre-commit").read_text() == foreign, "clobbered a peer guard"


def test_a_foreign_NON_guard_hook_is_preserved_and_chained(shared):
    """Somebody's real check must survive. It becomes .local and runs after the
    guard passes — in a worktree, which is where commits happen."""
    gitdir = guard.common_git_dir(shared)
    hooks = gitdir / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\ntouch \"$PWD/ran-local\"\nexit 0\n")
    (hooks / "pre-commit").chmod(0o755)

    guard.install(shared)
    assert (hooks / "pre-commit.local").is_file(), "destroyed a foreign hook"

    wt = shared.parent / "proj-wt" / "tester"
    assert _commit(wt, "chain").returncode == 0
    assert (wt / "ran-local").exists(), "the preserved hook never ran"


# --- discovery ----------------------------------------------------------------

def test_discovery_finds_repos_by_their_worktree_container(tmp_path):
    """st cannot know which repos are 'shared' in general — a bare clone with one
    user is not. What it knows is which it has PROVISIONED a worktree from."""
    for name in ("alpha", "beta"):
        (tmp_path / f"{name}-wt" / "someone").mkdir(parents=True)
        (tmp_path / name).mkdir()
    (tmp_path / "unrelated").mkdir()

    found = [p.name for p in guard.discover(tmp_path)]
    assert found == ["alpha", "beta"]
    assert "unrelated" not in found


def test_discovery_is_not_a_hardcoded_list(tmp_path):
    """The failure this whole check exists to catch: the deployment's installer
    defaulted to ONE repo, a by-hand audit found six, and discovery found twelve.
    A constant cannot cover the repo somebody clones tomorrow."""
    (tmp_path / "brand-new-wt" / "a").mkdir(parents=True)
    (tmp_path / "brand-new").mkdir()
    assert [p.name for p in guard.discover(tmp_path)] == ["brand-new"]


# --- the report ---------------------------------------------------------------

def test_the_report_never_says_protected(tmp_path):
    """`git reset` has no hook and two agents can stomp a working tree before any
    hook runs. A verdict implying safety would license the behaviour the guard
    exists to discourage."""
    rows = [guard.Coverage("a", tmp_path, guard.OK, why="guard installed")]
    text = guard.render(rows)
    assert "protected" not in text.lower()
    assert "guard installed" in text
    assert "git reset" in text and "COMMIT" in text


def test_the_report_calls_INERT_out_separately(tmp_path):
    rows = [
        guard.Coverage("a", tmp_path, guard.OK),
        guard.Coverage("b", tmp_path, guard.MISSING),
        guard.Coverage("c", tmp_path, guard.INERT, hooks_path="/x"),
    ]
    text = guard.render(rows)
    assert "INERT" in text
    assert "NOT 'missing'" in text, (
        "INERT folded into the unguarded count with no explanation reads as a "
        "repo nobody got round to")
    assert "1/3 guarded" in text


def test_exit_codes_rank_could_not_tell_above_a_real_gap(tmp_path):
    p = tmp_path
    assert guard.worst_exit([guard.Coverage("a", p, guard.OK)]) == 0
    assert guard.worst_exit([guard.Coverage("a", p, guard.MISSING)]) == 1
    assert guard.worst_exit([guard.Coverage("a", p, guard.INERT)]) == 1
    assert guard.worst_exit([guard.Coverage("a", p, guard.OK),
                             guard.Coverage("b", p, guard.CANNOT_TELL)]) == 2
    assert guard.worst_exit([]) == 0


def test_the_summary_can_never_read_as_a_pass_over_a_failing_report(tmp_path):
    """Caught live: the first version counted only MISSING/INERT as bad, so a run
    with two NO_CHECKOUT rows printed "12/12 carry the guard" while exiting 1.
    A summary agreeing with the exit code is the whole job of a summary."""
    rows = [guard.Coverage("a", tmp_path, guard.OK),
            guard.Coverage("ghost", tmp_path, guard.NO_CHECKOUT)]
    text = guard.render(rows)
    assert "2/2" not in text, "counted a non-repo as guarded"
    assert "1/1 carry the guard" in text
    assert "not counted" in text and "ghost" in text
    assert guard.worst_exit(rows) == 1, "and the exit code says it is not clean"


def test_a_fully_clean_survey_says_so_plainly(tmp_path):
    rows = [guard.Coverage("a", tmp_path, guard.OK),
            guard.Coverage("b", tmp_path, guard.OK)]
    assert "2/2 carry the guard" in guard.render(rows)
    assert guard.worst_exit(rows) == 0


def test_discovery_asks_GIT_not_the_container_NAME(tmp_path):
    """Found by running against a real fleet: two of twelve containers pointed
    somewhere else entirely — one at a differently-named repo, one at a repo
    OUTSIDE the root. Name inference reported both as 'no shared checkout' while
    the real repos went unguarded and unnamed."""
    real = tmp_path / "actual-repo"
    real.mkdir()
    _git(real, "init", "-q", "--initial-branch=main")
    _git(real, "config", "user.email", "t@t")
    _git(real, "config", "user.name", "t")
    (real / "f").write_text("f")
    _git(real, "add", "f")
    _git(real, "commit", "-qm", "init")
    # The container is named for something else entirely, and that something
    # else even EXISTS as a non-repo directory — the exact live shape.
    (tmp_path / "misleading").mkdir()
    _git(real, "worktree", "add", "-q", "-b", "wt/x",
         str(tmp_path / "misleading-wt" / "x"), "main")

    found = guard.discover(tmp_path)
    assert real in found, f"followed the name instead of git: {found}"
    assert (tmp_path / "misleading") not in found


def test_discovery_deduplicates_repos_reached_by_two_containers(tmp_path):
    """Two containers can hang off one repo. Reporting it twice would double-count
    the denominator, and installing twice is pointless work on a shared resource."""
    real = tmp_path / "one"
    real.mkdir()
    _git(real, "init", "-q", "--initial-branch=main")
    _git(real, "config", "user.email", "t@t")
    _git(real, "config", "user.name", "t")
    (real / "f").write_text("f")
    _git(real, "add", "f")
    _git(real, "commit", "-qm", "init")
    for c in ("aaa", "zzz"):
        _git(real, "worktree", "add", "-q", "-b", f"wt/{c}",
             str(tmp_path / f"{c}-wt" / c), "main")
    assert guard.discover(tmp_path).count(real) == 1
