"""stale_guard — the EDIT-TIME half of aegis-ib65p (decision 5).

Dispatch-time refresh is necessary and not sufficient: an agent dispatched at
19:00 may edit at 21:00 after main moved eight times, and an agent self-picking
off `bd ready` was never dispatched at all. So the check has to fire where the
act happens.

The three constraints are asserted, not hoped for: it must ADVISE (never block),
must NEVER pull under a live agent, and must be CHEAP (no fetch).
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

import pytest

from shantytown import stale_guard


def _run(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "init", "-q", "-b", "main")
    _run(path, "config", "user.email", "t@t"); _run(path, "config", "user.name", "t")
    (path / "seed.txt").write_text("seed")
    _run(path, "add", "-A"); _run(path, "commit", "-qm", "seed")
    return path


def _clone(src: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(src), str(dest)], check=True,
                   capture_output=True)
    _run(dest, "config", "user.email", "t@t"); _run(dest, "config", "user.name", "t")
    return dest


def _commit(path: Path, msg: str):
    (path / f"{msg}.txt").write_text(msg)
    _run(path, "add", "-A"); _run(path, "commit", "-qm", msg)


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """The throttle keys on the tree path and persists — without this, one test's
    notice would silence another's."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))


def test_a_behind_tree_is_ADVISED_with_the_duplication_risk_named(tmp_path):
    up = _repo(tmp_path / "up"); wt = _clone(up, tmp_path / "wt")
    _commit(up, "landed"); _run(wt, "fetch", "-q")

    msg = stale_guard.advise(wt / "seed.txt")
    assert msg and "BEHIND" in msg
    assert "already built what you are about to build" in msg


def test_it_says_it_did_NOT_pull_for_you(tmp_path):
    """Explicit in the decision. Rebasing under a live agent changes files out
    from under work in progress — a worse and less recoverable bug than
    staleness — so the message must state the non-action, not imply a fix."""
    up = _repo(tmp_path / "up"); wt = _clone(up, tmp_path / "wt")
    _commit(up, "landed"); _run(wt, "fetch", "-q")
    assert "NOT pulled for you" in stale_guard.advise(wt / "seed.txt")


def test_it_NEVER_modifies_the_tree(tmp_path):
    up = _repo(tmp_path / "up"); wt = _clone(up, tmp_path / "wt")
    _commit(up, "landed"); _run(wt, "fetch", "-q")
    before = _run(wt, "rev-parse", "HEAD")
    stale_guard.advise(wt / "seed.txt")
    assert _run(wt, "rev-parse", "HEAD") == before, "the guard moved HEAD"
    assert _run(wt, "status", "--porcelain") == "", "the guard dirtied the tree"


def test_UNPUSHED_work_is_advised_too_with_the_LOSS_risk(tmp_path):
    up = _repo(tmp_path / "up"); wt = _clone(up, tmp_path / "wt")
    _commit(wt, "only_local")
    msg = stale_guard.advise(wt / "seed.txt")
    assert msg and "NOT pushed" in msg
    # The claim is QUALIFIED to what has been fetched: `--remotes` only knows
    # refs this tree has. Four commits were reported at-risk on the live fleet
    # and a later fetch dropped the count to zero without a byte moving.
    assert "no remote ref this tree knows about" in msg


def test_a_CURRENT_tree_says_nothing_at_all(tmp_path):
    """The negative control that keeps it usable. An advisory that fires on a
    healthy tree is one everybody learns to ignore."""
    up = _repo(tmp_path / "up"); wt = _clone(up, tmp_path / "wt")
    assert stale_guard.advise(wt / "seed.txt") is None


def test_a_path_outside_any_repo_says_nothing(tmp_path):
    (tmp_path / "loose.txt").write_text("x")
    assert stale_guard.advise(tmp_path / "loose.txt") is None


def test_it_is_THROTTLED_so_the_same_notice_does_not_repeat_all_task(tmp_path):
    """The failure mode of an advisory is not being wrong, it is being CONSTANT.
    Forty identical lines in one task teach the reader to skip it."""
    up = _repo(tmp_path / "up"); wt = _clone(up, tmp_path / "wt")
    _commit(up, "landed"); _run(wt, "fetch", "-q")

    assert stale_guard.advise(wt / "seed.txt") is not None
    assert stale_guard.advise(wt / "seed.txt") is None, "it repeated immediately"


def test_a_CHANGED_finding_re_arms_the_throttle_at_once(tmp_path):
    """'now 3 behind' after '1 behind' is new information and must not be eaten
    by the quiet window."""
    up = _repo(tmp_path / "up"); wt = _clone(up, tmp_path / "wt")
    _commit(up, "one"); _run(wt, "fetch", "-q")
    assert stale_guard.advise(wt / "seed.txt") is not None
    _commit(up, "two"); _run(wt, "fetch", "-q")
    assert stale_guard.advise(wt / "seed.txt") is not None, "a worse finding was throttled"


def test_the_quiet_window_EXPIRES(tmp_path):
    up = _repo(tmp_path / "up"); wt = _clone(up, tmp_path / "wt")
    _commit(up, "landed"); _run(wt, "fetch", "-q")
    assert stale_guard.advise(wt / "seed.txt", now=1000.0) is not None
    assert stale_guard.advise(wt / "seed.txt", now=1000.0 + 60) is None
    later = 1000.0 + stale_guard.QUIET_SECONDS + 1
    assert stale_guard.advise(wt / "seed.txt", now=later) is not None


def test_it_does_not_FETCH(tmp_path):
    """The cost constraint. A hook that hit the network per edit gets switched
    off, and then it protects nothing."""
    up = _repo(tmp_path / "up"); wt = _clone(up, tmp_path / "wt")
    _commit(up, "landed_after_last_fetch")
    assert stale_guard.advise(wt / "seed.txt") is None, "the hook fetched"


def test_main_ALWAYS_exits_zero_even_on_garbage(tmp_path, monkeypatch, capsys):
    """ADVISE, NEVER BLOCK. An edit refused because a tree was behind would be a
    correctness rule enforced as an availability outage."""
    up = _repo(tmp_path / "up"); wt = _clone(up, tmp_path / "wt")
    _commit(up, "landed"); _run(wt, "fetch", "-q")

    class _In:
        def __init__(self, s): self._s = s
        def read(self): return self._s
        def isatty(self): return False

    monkeypatch.setattr("sys.stdin", _In(json.dumps(
        {"tool_input": {"file_path": str(wt / "seed.txt")}})))
    assert stale_guard.main([]) == 0
    assert "BEHIND" in capsys.readouterr().out

    monkeypatch.setattr("sys.stdin", _In("{not json"))
    assert stale_guard.main([]) == 0
    monkeypatch.setattr("sys.stdin", _In(""))
    assert stale_guard.main([]) == 0


def test_codex_apply_patch_payload_resolves_the_edited_file(tmp_path):
    """Codex sends a patch string, not Claude's tool_input.file_path."""
    payload = {
        "cwd": str(tmp_path),
        "tool_name": "apply_patch",
        "tool_input": {"command": (
            "*** Begin Patch\n"
            "*** Update File: src/changed.py\n"
            "@@\n-old\n+new\n"
            "*** End Patch")},
    }
    assert stale_guard._edited_path(payload) == tmp_path / "src/changed.py"


def test_codex_apply_patch_absolute_path_stays_absolute(tmp_path):
    target = tmp_path / "changed.py"
    payload = {
        "cwd": "/wrong",
        "tool_name": "apply_patch",
        "tool_input": {"command": f"*** Add File: {target}\n+content"},
    }
    assert stale_guard._edited_path(payload) == target


def test_a_linked_WORKTREE_resolves_to_itself_not_the_shared_checkout(tmp_path):
    """The tree the agent edits is the worktree. Resolving to the shared checkout
    would measure staleness of a tree nobody is working in."""
    up = _repo(tmp_path / "up"); shared = _clone(up, tmp_path / "shared")
    wt = tmp_path / "shared-wt" / "zia"
    subprocess.run(["git", "-C", str(shared), "worktree", "add", "-q", "-b",
                    "wt/zia", str(wt)], check=True, capture_output=True)
    assert stale_guard._repo_root(wt / "seed.txt") == wt.resolve()


# --- DELIVERY: it has to reach agents that are already running ---------------
#
# The measured lesson this codebase already paid for twice (aegis-rcyd): a hook
# wired into `claude_settings_for_role` is emitted only on `role set`, so running
# agents never regenerate it and it reaches nobody. The provision consent file is
# re-applied on EVERY launch and self-heals. A staleness guard that reached
# nobody would be a particularly bad joke, since not reaching people IS the bug.

def test_the_hook_is_delivered_via_PROVISION_so_it_self_heals(tmp_path):
    from shantytown.provision import _with_stale_hook
    out = json.loads(_with_stale_hook("{}", "worker", tmp_path))
    cmds = [h["command"] for e in out["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert any("shantytown.stale_guard" in c for c in cmds)


def test_ADMINISTRATORS_are_NOT_exempt_unlike_the_untracked_nudge(tmp_path):
    """The untracked nudge exempts admins because scolding a coordinator for
    dispatching rather than committing is role-specific. Staleness is not: the
    duplication that opened this bead was the COORDINATOR rebuilding a fix that
    already existed. Exempting that role would be exactly the wrong lesson."""
    from shantytown.provision import _with_stale_hook
    out = json.loads(_with_stale_hook("{}", "administrator", tmp_path))
    cmds = [h["command"] for e in out["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert any("shantytown.stale_guard" in c for c in cmds)


def test_re_provisioning_does_not_STACK_the_hook(tmp_path):
    """Claude Code merges hooks from every settings source; a stacked entry fires
    twice per tool call and doubles every notice."""
    from shantytown.provision import _with_stale_hook
    once = _with_stale_hook("{}", "worker", tmp_path)
    twice = _with_stale_hook(once, "worker", tmp_path)
    cmds = [h["command"] for e in json.loads(twice)["hooks"]["PreToolUse"]
            for h in e["hooks"]]
    assert sum("shantytown.stale_guard" in c for c in cmds) == 1


def test_the_matcher_excludes_Bash(tmp_path):
    """Bash carries no file_path, so the hook would fire on every shell command
    with nothing to measure — noise on the busiest matcher there is."""
    from shantytown.runtime import _stale_hook
    m = _stale_hook(tmp_path)["matcher"]
    assert "Edit" in m and "Write" in m and "Bash" not in m


# --- decision 7: tend refreshes WORKTREES at the respawn moment --------------

def test_tend_refreshes_worktrees_only_at_RESPAWN(tmp_path):
    """The only safe seam. Everywhere else a worktree refresh could race an
    agent mid-edit — which is why the edit-time guard only advises. At respawn
    the agent is provably DOWN, the same safety proof the workspace-clone
    refresh already relies on."""
    from shantytown import tend as tend_mod
    from shantytown.protocols import Agent

    called = []

    class _Panes:
        def __init__(self): self.made = []
        def exists(self, p): return False
        def capture(self, p): return ""
        def new_session(self, p, cwd=None): self.made.append(p)

    class _Rt:
        def shows_ready_ui(self, s): return False
        def is_ready(self, s): return True

    class _L:
        def get(self, n): return None
        def verdict(self, *a, **k): return "current"

    card = Agent(name="zia", role="worker", pane="p-zia")
    t = tend_mod.Tender(_Panes(), _Rt(), _L(), spawn=lambda c, p: None,
                        ensure=lambda c: None,
                        refresh_trees=lambda c: called.append(c.name) or [])
    t.pass_over([card])
    assert called == ["zia"], "the respawn path did not refresh worktrees"


def test_respawn_refresh_uses_actual_agent_trees_not_repo_name_roundtrip(
        tmp_path, monkeypatch):
    """The live ib65p gap: guard discovery found the repo correctly, then
    name-derived worktree_for silently mapped it back to a different container.
    The refresh consumer must receive the actual paths verbatim."""
    from types import SimpleNamespace
    from shantytown import cli

    actual = [tmp_path / "misleading-wt" / "zia",
              tmp_path / "outside-wt" / "zia"]
    refreshed = []
    monkeypatch.setattr(cli, "agent_worktrees", lambda name: actual)
    monkeypatch.setattr(cli, "_refresh_worktree",
                        lambda path: refreshed.append(path) or None)
    assert cli._refresh_agent_worktrees(SimpleNamespace(),
                                        SimpleNamespace(name="zia")) == []
    assert refreshed == actual


def test_a_worktree_refresh_failure_NEVER_blocks_the_respawn(tmp_path):
    """Same trade as the clone refresh: refusing to start an agent because a
    git fetch failed swaps a stale tree for an outage."""
    from shantytown import tend as tend_mod
    from shantytown.protocols import Agent

    class _Panes:
        def __init__(self): self.made = []
        def exists(self, p): return False
        def capture(self, p): return ""
        def new_session(self, p, cwd=None): self.made.append(p)

    class _Rt:
        def shows_ready_ui(self, s): return False
        def is_ready(self, s): return True

    class _L:
        def get(self, n): return None
        def verdict(self, *a, **k): return "current"

    panes = _Panes()
    card = Agent(name="zia", role="worker", pane="p-zia")
    def _boom(c): raise RuntimeError("git exploded")
    t = tend_mod.Tender(panes, _Rt(), _L(), spawn=lambda c, p: None,
                        ensure=lambda c: None, refresh_trees=_boom)
    rep = t.pass_over([card])
    assert panes.made == ["p-zia"], "a worktree refresh failure blocked the respawn"
    assert rep.findings[0].verdict == tend_mod.RESPAWNED
