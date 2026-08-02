"""upstream_ref / tree_staleness — WHICH ref means "current", and both directions.

Real git repos in tmp_path, not fakes. Every property here is a real-git
semantic (`@{upstream}` resolution, remote divergence, ahead/behind counts), and
a fake runner would only prove that the fake agrees with itself.

THE INCIDENT (aegis-ib65p). Twelve of twelve shantytown worktrees were behind,
and the coordinator rebuilt from scratch a fix an open PR already had. The first
remedy hardcoded `origin/main` — but on that repo `origin` was a public MIRROR
while `main` tracked `forge`, and which of the two led INVERTED within three
hours (mirror 1 behind, then 1 ahead). So the remedy would have rebased twelve
worktrees onto a ref missing real work while printing "current".
"""
from __future__ import annotations
import subprocess
from pathlib import Path

import pytest

from shantytown.workspace import Staleness, tree_staleness, upstream_ref


def _run(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout.strip()


def _repo(path: Path, name: str = "seed") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "init", "-q", "-b", "main")
    _run(path, "config", "user.email", "t@t")
    _run(path, "config", "user.name", "t")
    (path / f"{name}.txt").write_text(name)
    _run(path, "add", "-A")
    _run(path, "commit", "-qm", f"seed {name}")
    return path


def _commit(path: Path, msg: str):
    (path / f"{msg}.txt").write_text(msg)
    _run(path, "add", "-A")
    _run(path, "commit", "-qm", msg)


def _clone(src: Path, dest: Path, remote: str = "origin") -> Path:
    subprocess.run(["git", "clone", "-q", str(src), str(dest)], check=True,
                   capture_output=True)
    _run(dest, "config", "user.email", "t@t")
    _run(dest, "config", "user.name", "t")
    if remote != "origin":
        _run(dest, "remote", "rename", "origin", remote)
    return dest


# --- resolution -------------------------------------------------------------

def test_one_remote_resolves_to_it(tmp_path: Path):
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    ref, note = upstream_ref(wt)
    assert ref == "origin/main" and note is None


def test_the_CONFIGURED_upstream_wins_over_the_name_origin(tmp_path: Path):
    """THE regression. Two remotes, and `main` tracks the one NOT called origin.

    This is the exact shantytown topology: `origin` is a public mirror, `forge`
    is what main tracks. Resolving by the name `origin` picks the mirror.
    """
    forge = _repo(tmp_path / "forge")
    wt = _clone(forge, tmp_path / "wt", remote="forge")
    mirror = _repo(tmp_path / "mirror", name="mirror")
    _run(wt, "remote", "add", "origin", str(mirror))
    _run(wt, "fetch", "--all", "-q")
    _run(wt, "branch", "--set-upstream-to=forge/main", "main")

    ref, _ = upstream_ref(wt)
    assert ref == "forge/main", "resolved by remote NAME instead of by config"


def test_two_remotes_and_no_config_REFUSES_rather_than_guessing(tmp_path: Path):
    """A wrong ref is undetectable once the agent is working; a refusal is one
    line and a human fixes it. Same doctrine as normalize_source's guessed remote."""
    a = _repo(tmp_path / "a")
    b = _repo(tmp_path / "b", name="b")
    wt = tmp_path / "wt"
    wt.mkdir()
    _run(wt, "init", "-q", "-b", "main")
    _run(wt, "remote", "add", "one", str(a))
    _run(wt, "remote", "add", "two", str(b))
    _run(wt, "fetch", "--all", "-q")

    ref, note = upstream_ref(wt)
    assert ref is None, "guessed a remote instead of refusing"
    assert "REFUSING to guess" in note
    assert "one" in note and "two" in note, "the refusal must name both remotes"
    assert "set-upstream-to" in note, "a refusal must carry its fix"


def test_a_SECOND_remote_that_is_ahead_is_REPORTED_never_silently_ignored(tmp_path):
    """The measured shantytown case: main tracks forge, but the mirror had a
    commit forge did not. The chosen ref is still used — but staying quiet about
    work we do not have is the bead's own bug with a tidier surface."""
    forge = _repo(tmp_path / "forge")
    wt = _clone(forge, tmp_path / "wt", remote="forge")
    mirror = _clone(forge, tmp_path / "mirror_clone")
    _commit(mirror, "only_on_mirror")
    _run(wt, "remote", "add", "origin", str(mirror))
    _run(wt, "fetch", "--all", "-q")
    _run(wt, "branch", "--set-upstream-to=forge/main", "main")

    ref, note = upstream_ref(wt)
    assert ref == "forge/main", "the configured ref must still win"
    assert note and "origin/main is 1 ahead" in note
    assert "work exists that it does not contain" in note


def test_no_divergence_reports_no_note(tmp_path: Path):
    """Negative control — a warning that always fires is noise."""
    forge = _repo(tmp_path / "forge")
    wt = _clone(forge, tmp_path / "wt", remote="forge")
    _run(wt, "remote", "add", "origin", str(forge))
    _run(wt, "fetch", "--all", "-q")
    _run(wt, "branch", "--set-upstream-to=forge/main", "main")
    ref, note = upstream_ref(wt)
    assert ref == "forge/main" and note is None


# --- both directions --------------------------------------------------------

def test_BEHIND_is_reported_with_the_duplication_risk_named(tmp_path: Path):
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    _commit(up, "landed_upstream")
    _run(wt, "fetch", "-q")

    s = tree_staleness(wt)
    assert s.behind == 1 and s.unpushed == 0
    assert not s.current()
    assert "do NOT have" in s.render() and "duplication" in s.render()


def test_UNPUSHED_is_reported_with_the_LOSS_risk_named(tmp_path: Path):
    """The other direction, added because both were live in one evening. A tree
    that is only ahead is NOT 'fine' — that work exists in exactly one place."""
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    _commit(wt, "only_local")

    s = tree_staleness(wt)
    assert s.behind == 0 and s.unpushed == 1
    assert not s.current(), "a tree with stranded work read as current"
    assert "no remote ref KNOWN LOCALLY" in s.render()


def test_BOTH_directions_at_once(tmp_path: Path):
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    _commit(up, "theirs")
    _commit(wt, "mine")
    _run(wt, "fetch", "-q")

    s = tree_staleness(wt)
    assert s.behind == 1 and s.unpushed == 1
    r = s.render()
    assert "behind" in r and "no remote ref KNOWN LOCALLY" in r


def test_a_current_tree_says_so_and_dirt_alone_is_not_staleness(tmp_path: Path):
    """Uncommitted work is normal mid-task. If dirt counted as stale the signal
    would fire constantly and get ignored — but it IS surfaced, because it is
    why a refresh must report instead of act."""
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    (wt / "scratch.txt").write_text("wip")

    s = tree_staleness(wt)
    assert s.current(), "uncommitted work was counted as staleness"
    assert s.dirty and "uncommitted" in s.render()


def test_it_NEVER_writes(tmp_path: Path):
    """Called from an edit-time hook. A check that mutated the tree would be a
    correctness hazard under a live agent."""
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    _commit(up, "theirs")
    _run(wt, "fetch", "-q")
    before = _run(wt, "rev-parse", "HEAD")
    tree_staleness(wt)
    assert _run(wt, "rev-parse", "HEAD") == before


def test_it_does_NOT_fetch_by_default(tmp_path: Path):
    """Decision 5's cost constraint, asserted rather than hoped for: a hook that
    hit the network per edit gets switched off, and then protects nothing."""
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    _commit(up, "landed_after_last_fetch")

    assert tree_staleness(wt).behind == 0, "it fetched without being asked"
    assert tree_staleness(wt, fetch=True).behind == 1, "fetch=True did not fetch"


def test_unresolvable_upstream_reads_as_UNKNOWN_not_as_current(tmp_path: Path):
    """Silence must not flatter: a tree we could not measure must never render
    as a clean one."""
    a = _repo(tmp_path / "a"); b = _repo(tmp_path / "b", name="b")
    wt = tmp_path / "wt"; wt.mkdir()
    _run(wt, "init", "-q", "-b", "main")
    _run(wt, "remote", "add", "one", str(a)); _run(wt, "remote", "add", "two", str(b))

    s = tree_staleness(wt)
    assert not s.current()
    assert "UNKNOWN" in s.render()


# --- the st crew column (aegis-ib65p decision 6) -----------------------------

def _card(name, ws=None):
    from shantytown.protocols import Agent
    return Agent(name=name, role="worker", pane=f"p-{name}", workspace=ws)


def test_the_crew_cell_shows_both_directions_compactly(tmp_path):
    from shantytown import cli
    up = _repo(tmp_path / "up"); wt = _clone(up, tmp_path / "wt")
    _commit(up, "theirs"); _commit(wt, "mine"); _run(wt, "fetch", "-q")

    cell, detail = cli._tree_staleness_cell(None, _card("zia", str(wt)))
    assert cell == "-1/+1", f"cell was {cell!r}"
    assert detail and "vs origin/main" in detail


def test_a_current_tree_reads_ok_and_adds_no_detail_line(tmp_path):
    from shantytown import cli
    up = _repo(tmp_path / "up"); wt = _clone(up, tmp_path / "wt")
    cell, detail = cli._tree_staleness_cell(None, _card("zia", str(wt)))
    assert cell == "ok" and detail is None


def test_an_agent_with_no_workspace_reads_dash_not_ok(tmp_path):
    """`—` is 'no tree to measure'. Rendering it as `ok` would report health for
    something never looked at, which is the exact disease here."""
    from shantytown import cli
    cell, detail = cli._tree_staleness_cell(None, _card("ghost", None))
    assert cell == "—" and detail is None


def test_an_unresolvable_upstream_reads_QUESTION_MARK_never_ok(tmp_path):
    """`?` never rounds to `ok`. Invisible staleness read as fine is the bug."""
    from shantytown import cli
    a = _repo(tmp_path / "a"); b = _repo(tmp_path / "b", name="b")
    wt = tmp_path / "wt"; wt.mkdir()
    _run(wt, "init", "-q", "-b", "main")
    _run(wt, "remote", "add", "one", str(a)); _run(wt, "remote", "add", "two", str(b))

    cell, detail = cli._tree_staleness_cell(None, _card("zia", str(wt)))
    assert cell == "?" and detail and "UNKNOWN" in detail


def test_the_full_worktree_sweep_is_OPT_IN(tmp_path, monkeypatch):
    """`st crew` is the most-run command on the fleet; an unconditional sweep is
    ~3 git calls per agent per repo (>100 processes here) and would make the
    status read slow enough to be run less. It also made unit tests depend on
    which worktrees existed on the developer's machine — measured, it changed
    three existing tests' output."""
    from shantytown import cli
    calls = []
    monkeypatch.setattr(cli.guard_mod, "discover", lambda *a, **k: (calls.append(1) or []))
    up = _repo(tmp_path / "up"); wt = _clone(up, tmp_path / "wt")

    cli._agent_trees(None, _card("zia", str(wt)))
    assert calls == [], "the default path swept shared repos"
    cli._agent_trees(None, _card("zia", str(wt)), sweep=True)
    assert calls == [1], "--trees did not sweep"


def test_the_detail_names_the_REPO_not_the_agent(tmp_path):
    """A worktree lives at <repo>-wt/<agent>, so its basename is the AGENT.
    Rendering by basename produced "arnold -5; arnold -3; arnold -1/+15" on the
    live fleet — the same word three times, identifying none of the repos, which
    is most of what the sweep is for."""
    from shantytown import cli
    assert cli._tree_label("/home/x/gt/quipu-wt/arnold") == "quipu"
    assert cli._tree_label("/home/x/gt/shantytown-wt/zia") == "shantytown"
    assert cli._tree_label("/home/x/gt/beads_aegis/crew/zia") == "workspace"


def test_a_PUSHED_FEATURE_BRANCH_is_not_reported_as_stranded(tmp_path):
    """THE calibration regression (tim + dearing, aegis-ib65p).

    `<ref>..HEAD` measures ahead-of-main, not unpushed, so every in-flight
    feature branch on the fleet reported "+N, exists only here". Measured false
    positives: hank-wt/tim's commit was on origin/hank-1-daemon-stage3c, and
    dearing's four quipu commits were all on origin — pushed, mid-review,
    nothing at risk.

    It is the worst direction for THIS warning specifically: a loss alarm earns
    attention by being rare, and one that fires on every open branch trains
    everyone to dismiss it — including the one commit that really is stranded.
    """
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    _run(wt, "checkout", "-q", "-b", "feature")
    _commit(wt, "on_a_pushed_feature_branch")
    _run(wt, "push", "-q", "origin", "feature")
    _run(wt, "fetch", "-q")

    s = tree_staleness(wt)
    assert s.unpushed == 0, (
        "a commit pushed to origin/feature was reported as existing nowhere else")
    assert "no remote ref KNOWN LOCALLY" not in s.render()


def test_a_commit_on_NO_remote_ref_IS_still_reported(tmp_path):
    """The negative control — the fix must not silence the real case."""
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    _run(wt, "checkout", "-q", "-b", "feature")
    _commit(wt, "never_pushed_anywhere")

    s = tree_staleness(wt)
    assert s.unpushed == 1 and "no remote ref KNOWN LOCALLY" in s.render()
