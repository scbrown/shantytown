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
    (wt / "seed.txt").write_text("modified, tracked, uncommitted\n")

    s = tree_staleness(wt)
    assert s.current(), "uncommitted work was counted as staleness"
    assert s.dirty and "uncommitted" in s.render()


def test_untracked_files_are_not_dirty_and_are_reported_separately(tmp_path: Path):
    """aegis-4hwpdb. A stray file is not a tracked modification and must not be
    reported as one: `dirty` gates the cycle guard, whose remedy is "commit and
    push", and following that instruction on an untracked `.mcp.json.bak` commits
    a live bearer token."""
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    (wt / "scratch.txt").write_text("wip")
    (wt / ".mcp.json.bak-mcpfix").write_text("{}")

    s = tree_staleness(wt)
    assert not s.dirty, "an untracked stray was counted as a tracked modification"
    assert s.current()
    assert s.untracked_count == 2
    assert set(s.untracked) == {"scratch.txt", ".mcp.json.bak-mcpfix"}
    assert "uncommitted" not in s.render()


def test_untracked_all_names_files_inside_an_untracked_directory(tmp_path: Path):
    """The default porcelain collapses `dir/` to one entry, which is enough to
    count untracked work and NOT enough to name a credential inside it. The
    cycle path asks for the expansion; the edit-time hook must not, so it is a
    parameter rather than the default."""
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    (wt / ".playwright-mcp").mkdir()
    (wt / ".playwright-mcp" / "mcp.json.bak").write_text("{}")

    collapsed = tree_staleness(wt)
    assert collapsed.untracked == (".playwright-mcp/",)

    expanded = tree_staleness(wt, untracked_all=True)
    assert expanded.untracked == (".playwright-mcp/mcp.json.bak",)


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
    monkeypatch.setattr(cli, "agent_worktrees", lambda *a, **k: (calls.append(1) or []))
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


def test_a_commit_whose_ONLY_REMOTE_REF_WAS_DELETED_is_still_at_risk(tmp_path):
    """THE under-reporting regression (tim, aegis-ib65p) — the one direction a
    data-loss metric may not fail in.

    `git fetch` does NOT delete remote-tracking refs for branches deleted
    upstream, and fetch.prune is unset here at local, global AND system scope.
    So `refs/remotes/origin/<deleted>` survives, `--not --remotes` still counts
    commits reachable from it, and an orphan is laundered into "on a remote".

    Note the asymmetry against the two earlier corrections: those OVER-reported
    (noise, survivable). This one UNDER-reports — it calls a commit safe when it
    exists nowhere but this disk. And "as of the last fetch" does not cover it,
    because the ref that lies SURVIVES the fetch; only the prune removes it.
    """
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    _run(wt, "checkout", "-q", "-b", "doomed")
    _commit(wt, "only_on_a_branch_about_to_be_deleted")
    _run(wt, "push", "-q", "origin", "doomed")
    _run(wt, "fetch", "-q")
    assert tree_staleness(wt).unpushed == 0, "precondition: pushed, so safe"

    # The branch is deleted upstream. A PLAIN fetch leaves the local ref behind.
    _run(up, "branch", "-D", "doomed")
    _run(wt, "fetch", "-q")
    stale_ref = _run(wt, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/doomed")
    assert stale_ref, "precondition: a plain fetch left the dead ref in place"

    # fetch=True now prunes, so the orphan is seen for what it is.
    s = tree_staleness(wt, fetch=True)
    assert s.unpushed == 1, (
        "a commit whose only remote branch was DELETED was reported as safe")


def test_the_prune_does_not_invent_risk_for_a_live_branch(tmp_path):
    """Negative control: pruning must not flag work that is genuinely pushed."""
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    _run(wt, "checkout", "-q", "-b", "alive")
    _commit(wt, "pushed_and_kept")
    _run(wt, "push", "-q", "origin", "alive")
    assert tree_staleness(wt, fetch=True).unpushed == 0


def test_the_cycle_guard_passes_a_real_clone_holding_only_a_secret_stray(tmp_path: Path):
    """END TO END on real git, aegis-4hwpdb. The unit tests inject a stand-in for
    Staleness; this one proves the two halves agree — that a real clone with wu's
    actual stray in it reaches the guard as untracked-not-dirty, cycles, and is
    reported with the do-not-commit marker.

    Both halves were correct in isolation before this bead: `git status
    --porcelain` reported the stray accurately and the guard refused accurately
    on what it was handed. The defect lived in the seam."""
    from shantytown.cycle import assess

    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    (wt / ".mcp.json.bak-mcpfix").write_text('{"token": "REDACTED"}')

    v = assess("wu", [wt], "mid-way through the mcp fix",
               staleness=lambda t: tree_staleness(t, untracked_all=True))
    assert v.ok, v.render()
    assert ".mcp.json.bak-mcpfix" in v.render()
    assert "do NOT `git add` this" in v.render()

    (wt / "seed.txt").write_text("a tracked modification\n")
    v2 = assess("wu", [wt], "mid-way through the mcp fix",
                staleness=lambda t: tree_staleness(t, untracked_all=True))
    assert not v2.ok and "would be lost" in v2.reason


# --- provisioned dirt (aegis-c14kn6) -----------------------------------------
#
# st writes a delimited tooling block into TRACKED CLAUDE.md. That left 13 of 13
# crew clones permanently dirty and refused every cycle, while the rulebook
# forbids committing that file — so the refusal advised the one action the agent
# must never take. 10 of the 13 held ZERO unpushed commits, i.e. this stranded
# them with every remote healthy.
#
# The weight here is deliberately on the arms that must STILL GATE. Waving
# provisioned dirt through is only safe if it cannot be stretched to cover an
# agent's own work, and each test below is one way that stretch could happen.

from shantytown.tooling import BEGIN, END
from shantytown.workspace import strip_tooling_block


def _tree_with_claude_md(tmp_path, body="base line\n"):
    src = _repo(tmp_path / "src", "seed")
    (src / "CLAUDE.md").write_text(body)
    _run(src, "add", "-A")
    _run(src, "commit", "-qm", "add CLAUDE.md")
    return _clone(src, tmp_path / "work")


def _block(text="tooling instructions"):
    return f"\n{BEGIN}\n## {text}\n{END}\n"


def test_a_provisioned_block_alone_is_NOT_dirty(tmp_path):
    """The whole bug: st's own writing read as the agent's unsaved work."""
    w = _tree_with_claude_md(tmp_path)
    (w / "CLAUDE.md").write_text("base line\n" + _block())
    s = tree_staleness(w)
    assert s.dirty is False
    assert s.provisioned == ("CLAUDE.md",)


def test_an_edit_OUTSIDE_the_block_still_gates(tmp_path):
    """THE ARM THAT MATTERS. A provisioned block must not launder the agent's own
    edit to the same file — that edit is real work and losing it is what the gate
    is for."""
    w = _tree_with_claude_md(tmp_path)
    (w / "CLAUDE.md").write_text("base line\nMY OWN NOTE\n" + _block())
    s = tree_staleness(w)
    assert s.dirty is True
    assert s.provisioned == ()


def test_another_modified_file_alongside_still_gates(tmp_path):
    """The exemption is per-TREE, not per-file: any non-provisioned modification
    anywhere in the tree keeps the gate up, so a provisioned block cannot make a
    dirty tree read clean."""
    w = _tree_with_claude_md(tmp_path)
    (w / "CLAUDE.md").write_text("base line\n" + _block())
    (w / "seed.txt").write_text("changed by the agent")
    s = tree_staleness(w)
    assert s.dirty is True
    assert s.provisioned == ()


def test_an_unterminated_block_still_gates(tmp_path):
    """A BEGIN with no END is a malformed tree, not a clean one. If stripping
    swallowed the tail, everything after a stray marker would compare equal and
    real work would vanish through the exemption."""
    w = _tree_with_claude_md(tmp_path)
    (w / "CLAUDE.md").write_text(
        "base line\n" + BEGIN + "\nhalf a block\nMY OWN NOTE\n")
    s = tree_staleness(w)
    assert s.dirty is True


def test_a_block_in_a_NON_provisioned_file_still_gates(tmp_path):
    """The allowlist is by PATH. Pasting the marker into another tracked file
    must not buy an exemption for it."""
    w = _tree_with_claude_md(tmp_path)
    (w / "seed.txt").write_text("seed" + _block())
    s = tree_staleness(w)
    assert s.dirty is True
    assert s.provisioned == ()


def test_unpushed_is_untouched_by_the_exemption(tmp_path):
    """This changes the DIRTY arm only. A tree carrying a real unpushed commit
    must still be reported as holding work that exists in one place."""
    w = _tree_with_claude_md(tmp_path)
    _commit(w, "real-work")
    (w / "CLAUDE.md").write_text("base line\n" + _block())
    s = tree_staleness(w)
    assert s.dirty is False
    assert s.unpushed == 1


def test_strip_tooling_block_is_reversible_on_a_clean_file(tmp_path):
    """A file with no block must come back unchanged apart from trailing space —
    otherwise every unmodified CLAUDE.md would compare unequal to itself."""
    assert strip_tooling_block("a\nb\n") == "a\nb"
    assert strip_tooling_block(f"a\n{BEGIN}\nx\n{END}\nb\n") == "a\nb"


def test_a_block_only_change_to_a_NON_provisioned_file_still_gates_alongside_one(tmp_path):
    """The hardest arm, and the one the other tests cannot reach.

    The allowlist is enforced TWICE — once over the whole tracked set in
    `tree_staleness`, once per path in `provisioned_only`. Either alone closes
    this, so mutating one is behaviour-equivalent and no single-guard mutation can
    be caught. That is defence in depth, not redundancy to be tidied away, and it
    is only worth anything if something demonstrates the property it defends.

    Here a provisioned file AND a non-provisioned one BOTH carry a change
    confined to a tooling block. Every earlier arm fails the inner check for an
    unrelated reason (the sibling's content differs outside any block), so this is
    the only case where removing both guards would actually wave real work
    through. Pasting the marker into a file st does not own must never buy it an
    exemption."""
    w = _tree_with_claude_md(tmp_path)
    (w / "CLAUDE.md").write_text("base line\n" + _block())
    (w / "seed.txt").write_text("seed" + _block("not st's file"))
    s = tree_staleness(w)
    assert s.dirty is True
    assert s.provisioned == ()


# --- remote_reachable: three-state, and None is the load-bearing one (tig80i) --

from shantytown.workspace import remote_reachable


def test_a_reachable_remote_reads_True(tmp_path):
    src = _repo(tmp_path / "src", "seed")
    w = _clone(src, tmp_path / "work")
    assert remote_reachable(w) is True


def test_an_unreachable_remote_reads_False(tmp_path):
    """Measured failure — the only state permitted to stand down the loss gate."""
    src = _repo(tmp_path / "src", "seed")
    w = _clone(src, tmp_path / "work")
    _run(w, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    assert remote_reachable(w) is False


def test_NO_remote_reads_None_and_not_False(tmp_path):
    """A tree with nowhere to push is not "the forge is down". Returning False
    here would let an unrelated situation stand down the gate — the exemption is
    for work that CANNOT be pushed right now, not work that was never going
    anywhere from this tree."""
    w = _repo(tmp_path / "solo", "seed")
    assert remote_reachable(w) is None


def test_one_reachable_remote_is_enough(tmp_path):
    """`st push` contacts every trusted peer, so somewhere-to-push means the
    remedy the gate advises can still succeed and the gate should stand."""
    src = _repo(tmp_path / "src", "seed")
    w = _clone(src, tmp_path / "work")
    _run(w, "remote", "add", "dead", str(tmp_path / "gone.git"))
    assert remote_reachable(w) is True


def test_the_probe_is_cached_per_url(tmp_path):
    """Worktrees mostly share one forge; the honest answer for the second tree is
    the answer measured for the first, and a per-tree probe would multiply a
    10-second timeout across a fleet on the cycle path."""
    src = _repo(tmp_path / "src", "seed")
    w = _clone(src, tmp_path / "work")
    cache: dict = {}
    assert remote_reachable(w, cache=cache) is True
    assert len(cache) == 1
    # Poison the cached verdict: a second call must READ it, not re-probe.
    for url in list(cache):
        cache[url] = False
    assert remote_reachable(w, cache=cache) is False


# --- a timeout must kill the whole process GROUP (aegis-ujz5gf) ---------------

import os
import signal as _signal
import subprocess as _sp
import time as _time

from shantytown.workspace import run_with_group_timeout


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def test_a_timeout_kills_the_GRANDCHILD_not_just_the_child(tmp_path):
    """THE BUG, reproduced without git.

    `subprocess.run(timeout=)` kills only the direct child. `git pull` had by
    then spawned `git fetch`, which had spawned `ssh`; those survived, were
    adopted by `systemd --user`, and hung forever against the unreachable forge —
    45 of them, one per dispatch, with no upper bound.

    Here a shell spawns a long-lived grandchild and writes its pid, then sleeps
    past the timeout. The grandchild must be dead afterwards."""
    pidfile = tmp_path / "grandchild.pid"
    script = (f"sleep 300 & echo $! > {pidfile}; sleep 300")
    with __import__("pytest").raises(_sp.TimeoutExpired):
        run_with_group_timeout(["sh", "-c", script], 2,
                               stdout=_sp.PIPE, stderr=_sp.PIPE, text=True)
    for _ in range(50):                       # the kill is not instantaneous
        if pidfile.exists():
            break
        _time.sleep(0.1)
    assert pidfile.exists(), "the probe never recorded a grandchild pid"
    gpid = int(pidfile.read_text().strip())
    for _ in range(50):
        if not _alive(gpid):
            break
        _time.sleep(0.1)
    assert not _alive(gpid), (
        f"grandchild {gpid} survived the timeout — it would be reparented to "
        f"the subreaper and hang forever, which is aegis-ujz5gf")


def test_the_killer_does_not_kill_ITSELF(tmp_path):
    """`start_new_session=True` is what makes killpg safe as well as possible.
    Without it the child shares OUR process group, and killing that group takes
    down the caller — the same self-matching hazard as a `pkill -f` pattern that
    matches the watcher. This test passing at all means we survived."""
    with __import__("pytest").raises(_sp.TimeoutExpired):
        run_with_group_timeout(["sh", "-c", "sleep 300"], 1,
                               stdout=_sp.PIPE, stderr=_sp.PIPE, text=True)
    assert os.getpid() > 0                    # reached = the caller is alive
    assert _alive(os.getpid())


def test_a_normal_command_is_unaffected():
    """The helper replaces subprocess.run on hot paths, so the ordinary case must
    behave identically — same returncode, same captured stdout."""
    r = run_with_group_timeout(["sh", "-c", "echo hello; exit 3"], 30,
                               stdout=_sp.PIPE, stderr=_sp.PIPE, text=True)
    assert r.returncode == 3
    assert r.stdout.strip() == "hello"


def test_a_failing_kill_never_raises(tmp_path, monkeypatch):
    """Every failure inside the kill is benign — already gone, not permitted, no
    killpg on this platform. It must not turn a timeout into a crash on the
    dispatch path, which never raises by design."""
    from shantytown import workspace as W
    monkeypatch.setattr(W.os, "killpg",
                        lambda *a: (_ for _ in ()).throw(PermissionError()))
    with __import__("pytest").raises(_sp.TimeoutExpired):
        run_with_group_timeout(["sh", "-c", "sleep 30"], 1,
                               stdout=_sp.PIPE, stderr=_sp.PIPE, text=True)


# --- a fetch we asked for and did not get (aegis-bqcjws sibling, wu 2026-09-11) ---

def test_a_FAILED_fetch_reports_UNKNOWN_and_never_current(tmp_path: Path):
    """THE REGRESSION. fetch=True, the fetch fails, the ref is FROZEN.

    `rev-list HEAD..origin/main` against a frozen ref compares the tree against
    ITSELF and returns 0, which renders as "current with origin/main". So the
    failure is not a missing answer but a confident wrong one, pointing the
    reassuring way.

    This is built to be a LIE DETECTOR, not a smoke test: upstream is genuinely
    one commit ahead at assert time, so "current" would be false about the world,
    and `behind == 0` is what the old code actually returned here.
    """
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")

    # Upstream really advances. The clone has NOT fetched, so its remote-tracking
    # ref still points at the old tip.
    _commit(up, "work-the-clone-does-not-have")

    # CONTROL: with the remote reachable, a fetch=True read SEES that commit.
    # Without this arm the test could pass on a tree that was never behind.
    good = tree_staleness(wt, fetch=True)
    assert good.error is None, f"control arm should not error: {good.error}"
    assert good.behind == 1, f"control arm must be 1 behind, got {good.behind}"

    # Now rewind the clone's knowledge and make the remote unreachable, which is
    # the fleet state during the forge outage: keep-current's pull is refused, so
    # the remote-tracking ref freezes.
    _run(wt, "update-ref", "refs/remotes/origin/main", "HEAD")
    _run(wt, "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"))

    frozen = tree_staleness(wt, fetch=True)

    # The old behaviour, stated so a regression is unmistakable: a frozen ref
    # yields behind == 0, and 0 renders as "current".
    assert not frozen.current(), (
        "a tree whose fetch FAILED must never read as current — this is the "
        "exact false-green that rendered crew agents 'current' at 21 and 16 "
        "commits behind")
    # THE FIELD MOVED AND THE PROPERTY DID NOT (aegis-5ewwhl). A failed fetch
    # used to set `error`, which `cycle.assess` reads as "this tree could not be
    # read at all" — so during the outage every tree became a risk and `st cycle`
    # refused fleet-wide on clean trees with nothing unpushed. It now sets
    # `unverified`, which says precisely which HALF was unmeasurable.
    #
    # Everything this test was written to protect is asserted below, on
    # behaviour rather than on which attribute carries it.
    assert frozen.unverified, "a failed fetch must mark the behind-count unmeasured"
    assert "fetch failed" in frozen.unverified
    assert "current with" not in frozen.render()
    assert "currency UNVERIFIED" in frozen.render()
    # The behind-count is not merely wrong, it must not be PRINTED. `behind == 0`
    # off a frozen ref is the lie; rendering it beside an UNVERIFIED banner would
    # still hand a reader a number to believe.
    import re as _re
    assert not _re.search(r"\d+ behind ", frozen.render()), (
        f"no behind-COUNT may be printed: {frozen.render()}")

    # ...AND THE LOSS-RISK HALF IS STILL MEASURED, which is what lets the cycle
    # gate keep working through an outage. `HEAD --not --remotes` reads refs we
    # already hold, so a frozen ref can only over-report it, never under-report.
    assert frozen.unpushed == 0 and not frozen.dirty, (
        "a clean tree with nothing unpushed must be measured as such even when "
        "the fetch failed — refusing here is what blocked every agent's cycle")

    _commit(wt, "local-work-that-is-on-no-remote")
    stranded = tree_staleness(wt, fetch=True)
    assert stranded.unverified, "still a dead remote"
    assert stranded.unpushed == 1, (
        "loss risk must survive a failed fetch — this is the ONE signal the "
        "cycle gate exists for")


def test_a_SUCCESSFUL_fetch_is_unaffected(tmp_path: Path):
    """The fix must not make every reachable tree read UNKNOWN.

    Without this arm, returning `error` unconditionally would pass the test
    above while breaking staleness for the whole fleet.
    """
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    s = tree_staleness(wt, fetch=True)
    assert s.error is None, f"reachable remote must not error: {s.error}"
    assert s.current(), f"a freshly cloned tree is current, got {s.render()}"
    assert "current with" in s.render()


def test_fetch_FALSE_is_still_the_cheap_honest_read(tmp_path: Path):
    """fetch=False never touches the network, so a dead remote is irrelevant to
    it — the edit-time hook path must stay free and must NOT start erroring."""
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    _run(wt, "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"))
    s = tree_staleness(wt, fetch=False)
    assert s.error is None, f"fetch=False must not error: {s.error}"
    assert s.current()


# --- the FETCHLESS half: a frozen ref must not render "current" -------------
# wu, 2026-09-11, verifying the fetch=True fix rather than taking it on report:
# `st crew`'s DEFAULT column passes sweep=False, so it never fetches — there is
# no failed fetch to key UNKNOWN off, and a ref frozen by a two-day forge outage
# still rendered "ok". Measured on grant: ref frozen at 95f92e0c, 16 commits
# behind, cell "ok".

def test_ref_mtime_moves_on_a_SUCCESSFUL_fetch_and_not_on_a_failed_one(tmp_path: Path):
    """THE MEASUREMENT the remedy rests on, and why it is not FETCH_HEAD.

    A FAILED fetch still writes FETCH_HEAD, so FETCH_HEAD answers "when did we
    last TRY" — during an outage that is "seconds ago, continuously", which
    would report a frozen ref as fresh and rebuild the very defect this closes.
    The ref itself only moves when the remote actually answered.
    """
    from shantytown.workspace import ref_last_updated
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")

    _commit(up, "more")
    tree_staleness(wt, fetch=True)          # a SUCCESSFUL fetch
    after_ok = ref_last_updated(wt, "origin/main")
    assert after_ok is not None

    fetch_head = Path(wt) / ".git" / "FETCH_HEAD"
    fh_before = fetch_head.stat().st_mtime if fetch_head.exists() else None

    _run(wt, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    tree_staleness(wt, fetch=True)          # a FAILED fetch
    after_fail = ref_last_updated(wt, "origin/main")

    assert after_fail == after_ok, (
        "a FAILED fetch must not advance the ref mtime — if it did, this signal "
        "would report a frozen ref as freshly measured")
    if fh_before is not None and fetch_head.exists():
        assert fetch_head.stat().st_mtime >= fh_before, (
            "control: FETCH_HEAD is touched by the failed fetch, which is "
            "precisely why it cannot be the freshness signal")


def test_a_FROZEN_ref_does_not_render_as_current(tmp_path: Path, monkeypatch):
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    s = tree_staleness(wt, fetch=False)
    assert not s.measurement_is_stale(), "control: a fresh clone is fresh"
    assert "current with" in s.render()

    # age the reading past the horizon without touching the clock
    import shantytown.workspace as W
    monkeypatch.setattr(W, "MEASUREMENT_HORIZON_SECONDS", -1)
    s2 = tree_staleness(wt, fetch=False)
    assert s2.measurement_is_stale()
    assert "current with" not in s2.render()
    assert "LAST SUCCESSFUL FETCH" in s2.render()


def test_an_UNKNOWN_age_is_treated_as_stale_not_as_fresh():
    """Rounding "we could not tell" down to "recent" is the flattery this whole
    class is made of — same direction as CANNOT TELL IS NOT CLEAN."""
    assert Staleness(ref="origin/main", measured_age=None).measurement_is_stale()
    assert not Staleness(ref="origin/main", measured_age=1.0).measurement_is_stale()


# --------------------------------------------------------------------------
# aegis-8m3hig follow-on: the AHEAD side is unmeasured too when the fetch failed
# --------------------------------------------------------------------------

def test_a_failed_fetch_makes_the_AHEAD_count_a_bound_not_a_fact(tmp_path: Path):
    """During the forge outage every HTTPS landing rendered as unpushed, because
    `HEAD --not --remotes` reads frozen remote-tracking refs. One agent read +3
    against a tree identical to the remote; this author's own clone reported 40
    when the truth was 1.

    The count is not dropped — it is a real UPPER BOUND, and a reader who sees
    "up to N" knows both that it is not a fact and that it is not zero.
    """
    up = _repo(tmp_path / "up")
    wt = _clone(up, tmp_path / "wt")
    _commit(wt, "landed-by-another-transport")   # on no remote ref THIS tree knows

    # CONTROL: with the remote reachable the count is a plain fact.
    good = tree_staleness(wt, fetch=True)
    assert good.unverified is None
    assert good.unpushed == 1
    assert "up to" not in good.render()
    assert "1 on no remote ref" in good.render()

    _run(wt, "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"))
    frozen = tree_staleness(wt, fetch=True)

    assert frozen.unverified, "fetch failed, so the reading is unverified"
    assert frozen.unpushed == 1, "the RAW count must survive — the cycle gate consumes it"
    r = frozen.render()
    assert "up to 1 local commit(s) may be unpushed" in r
    assert "UPPER BOUND" in r
    # The bare factual phrasing must be gone, or the reader still reads it as measured.
    assert "1 on no remote ref KNOWN LOCALLY" not in r


def test_the_edit_time_advisory_goes_QUIET_on_an_unverified_reading(tmp_path: Path):
    """stale_guard runs on every edit. Its own docstring argues that an advisory
    which fires constantly and is never actionable is how the channel gets
    ignored — and during the outage its unpushed warning was false fleet-wide."""
    from shantytown import stale_guard
    from shantytown.workspace import Staleness

    import shantytown.workspace as ws

    fresh = Staleness(ref="origin/main", unpushed=2, measured_age=10.0)
    old_ref = Staleness(ref="origin/main", unpushed=2, measured_age=10**7)
    unver = Staleness(ref="origin/main", unpushed=2, measured_age=10.0,
                      unverified="fetch failed against origin/main")

    def _advise(st_obj, monkey):
        monkey.setattr(ws, "tree_staleness", lambda *a, **k: st_obj)
        monkey.setattr(stale_guard, "_repo_root", lambda p: Path("/r"))
        monkey.setattr(stale_guard, "_should_report", lambda *a, **k: True)
        return stale_guard.advise(Path("/r/f.py"), now=0.0)

    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    try:
        # CONTROL: a FRESH measured unpushed count still warns, or the two
        # silences below would pass against a guard that never fires at all.
        assert _advise(fresh, mp) is not None
        assert _advise(old_ref, mp) is None, "a stale ref makes the count untrustworthy"
        assert _advise(unver, mp) is None, "so does an explicitly failed fetch"
    finally:
        mp.undo()


# --------------------------------------------------------------------------
# aegis-l5y2hk: the sweep must DEGRADE during an outage, not stall on it
# --------------------------------------------------------------------------

def test_the_sweep_SKIPS_the_fetch_when_the_remote_is_measured_unreachable(tmp_path, monkeypatch):
    """`st crew --trees` fetches, and a fetch at a dead forge burns the full 60s
    git timeout PER TREE — ~40 forge-hosted worktrees is ~40 minutes for one
    status read. So the sweep is disabled by exactly the outage that makes it
    necessary, and during the 2026-09-09 outage two agents hand-rolled sweeps
    instead and produced three wrong answers between them.
    """
    from shantytown import cli
    up = _repo(tmp_path / "up"); wt = _clone(up, tmp_path / "wt")
    _commit(wt, "mine-only")                       # real unpushed work to find

    fetched = []
    real = cli.tree_staleness
    monkeypatch.setattr(cli, "tree_staleness",
                        lambda t, **kw: (fetched.append(kw.get("fetch")), real(t, **kw))[1])
    monkeypatch.setattr(cli, "remote_reachable", lambda *a, **k: False)

    cell, detail = cli._tree_staleness_cell(None, _card("zia", str(wt)), sweep=True)

    # `sweep=True` discovers this agent's worktree off every shared repo on the
    # host, so the count is environmental — assert the PROPERTY (no tree fetched)
    # rather than a tree count that varies with the box.
    assert fetched and all(f is False for f in fetched), \
        f"every doomed fetch must be SKIPPED, got {fetched}"
    assert cell == "?", "a tree we could not refresh must never read ok"
    assert detail and "remote unreachable" in detail
    assert "fetchless read" in detail


def test_a_could_not_tell_reachability_STILL_FETCHES(tmp_path, monkeypatch):
    """`remote_reachable` is three-state and its contract says None must be
    treated as reachable. Only a MEASURED False may skip work — uncertainty
    resolves toward doing it, which is the opposite direction from the skip."""
    from shantytown import cli
    up = _repo(tmp_path / "up"); wt = _clone(up, tmp_path / "wt")

    fetched = []
    real = cli.tree_staleness
    monkeypatch.setattr(cli, "tree_staleness",
                        lambda t, **kw: (fetched.append(kw.get("fetch")), real(t, **kw))[1])
    monkeypatch.setattr(cli, "remote_reachable", lambda *a, **k: None)
    cli._tree_staleness_cell(None, _card("zia", str(wt)), sweep=True)
    assert fetched and all(f is True for f in fetched), \
        f"could-not-tell must still fetch, got {fetched}"

    # CONTROL: a reachable remote fetches too, so the assertion above is not
    # passing merely because this code path always fetches.
    fetched.clear()
    monkeypatch.setattr(cli, "remote_reachable", lambda *a, **k: True)
    cli._tree_staleness_cell(None, _card("zia", str(wt)), sweep=True)
    assert fetched and all(f is True for f in fetched)


def test_the_DEFAULT_column_never_probes_reachability(tmp_path, monkeypatch):
    """The bead is explicit that the cost argument for the fetchless default
    still holds: do not fix this by making the most-run command on the fleet do
    network probes."""
    from shantytown import cli
    up = _repo(tmp_path / "up"); wt = _clone(up, tmp_path / "wt")
    probed = []
    monkeypatch.setattr(cli, "remote_reachable",
                        lambda *a, **k: probed.append(1) or False)
    cli._tree_staleness_cell(None, _card("zia", str(wt)))          # sweep=False
    assert probed == [], "the default column must not probe the network"
