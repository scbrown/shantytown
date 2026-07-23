"""Keep Current, mechanized (internal-ref): st pulls the agent's workspace at the
safe moments — assignment, relaunch, cycle — so currency stops depending on the
agent remembering. ff-only ALWAYS; the provisioned kit survives the pull; a
refused pull is VISIBLE and never blocks the work.
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

import pytest

from shantytown import cli
from shantytown.notify import CycleDriver
from shantytown.protocols import Agent
from shantytown.runtime import LiveWiring


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo_pair(tmp_path):
    """origin with one commit; clone one commit BEHIND after origin advances."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(tmp_path, "init", "-q", "-b", "main", str(origin))
    _git(origin, "config", "user.email", "t@example.invalid")
    _git(origin, "config", "user.name", "t")
    (origin / "a.txt").write_text("one\n")
    _git(origin, "add", "a.txt")
    _git(origin, "commit", "-q", "-m", "one")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    _git(clone, "config", "user.email", "t@example.invalid")
    _git(clone, "config", "user.name", "t")
    (origin / "a.txt").write_text("two\n")
    _git(origin, "commit", "-q", "-am", "two")
    return origin, clone


# --- _refresh_clone: ff-only, kit-preserving, loud-not-raising ---------------

def test_a_behind_clone_is_brought_current(repo_pair):
    _origin, clone = repo_pair
    assert cli._refresh_clone(clone) is None
    assert (clone / "a.txt").read_text() == "two\n"


def test_the_provisioned_kit_survives_the_pull(repo_pair):
    """.mcp.json is uncommitted BY DESIGN (bearer token). No pull outcome may
    strip it — an agent that comes out of keep-current without its tools is the
    five-agents-worked-a-night-without-tools class through a new door."""
    _origin, clone = repo_pair
    kit = clone / ".mcp.json"
    kit.write_text('{"token": "live-secret"}')
    assert cli._refresh_clone(clone) is None
    assert kit.read_text() == '{"token": "live-secret"}'


def test_the_kit_is_restored_even_when_history_replaces_it(repo_pair):
    """origin ships a TRACKED .mcp.json template over the untracked live one:
    git refuses the pull (untracked would be overwritten) OR overwrites — either
    way the LIVE token file must win locally, and the refusal must be loud."""
    origin, clone = repo_pair
    (origin / ".mcp.json").write_text('{"template": true}')
    _git(origin, "add", ".mcp.json")
    _git(origin, "commit", "-q", "-m", "template kit")
    kit = clone / ".mcp.json"
    kit.write_text('{"token": "live-secret"}')
    err = cli._refresh_clone(clone)
    assert kit.read_text() == '{"token": "live-secret"}', "the live kit must survive"
    assert err is not None, "a refused pull must be reported, not silent"


def test_local_tracked_dirt_refuses_loudly_and_never_forces(repo_pair):
    """The internal-ref condition: diverged tracked content. ff-only refuses;
    the local work SURVIVES (never force/reset — internal-ref/iaef)."""
    _origin, clone = repo_pair
    (clone / "a.txt").write_text("local uncommitted work\n")
    err = cli._refresh_clone(clone)
    assert err is not None
    assert (clone / "a.txt").read_text() == "local uncommitted work\n"


def test_not_a_repo_is_an_error_string_not_an_exception(tmp_path):
    assert cli._refresh_clone(tmp_path / "nowhere") is not None


# --- _keep_current: the dispatch-time wrapper --------------------------------

class _Reg:
    def __init__(self, cards):
        self._c = {a.name: a for a in cards}
    def get(self, name):
        return self._c[name]
    def all(self):
        return list(self._c.values())


def test_keep_current_pulls_the_agents_workspace(repo_pair, monkeypatch):
    _origin, clone = repo_pair
    reg = _Reg([Agent(name="w", role="worker", workspace=str(clone))])
    monkeypatch.setattr(cli, "_registry", lambda a: reg)
    assert cli._keep_current(object(), "w") is None
    assert (clone / "a.txt").read_text() == "two\n"


def test_a_refused_pull_warns_and_names_the_workspace(repo_pair, monkeypatch):
    _origin, clone = repo_pair
    (clone / "a.txt").write_text("dirt\n")
    reg = _Reg([Agent(name="w", role="worker", workspace=str(clone))])
    monkeypatch.setattr(cli, "_registry", lambda a: reg)
    warn = cli._keep_current(object(), "w")
    assert warn is not None
    assert "stale" in warn and str(clone) in warn
    assert "dispatching anyway" in warn, "a refused pull must not block the dispatch"


def test_no_workspace_is_nothing_to_pull_not_a_failure(monkeypatch):
    reg = _Reg([Agent(name="w", role="worker")])
    monkeypatch.setattr(cli, "_registry", lambda a: reg)
    assert cli._keep_current(object(), "w") is None


# --- the cycle pulls too -----------------------------------------------------

IDLE_SAT = ("❯ \n"
            "                  new task? /clear to save 687.0k tokens\n"
            "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents")


class _Panes:
    def __init__(self, screens):
        self._screens = screens
        self.sent = []
    def exists(self, pane):
        return pane in self._screens
    def capture(self, pane, history=0, attrs=False):
        return self._screens.get(pane, "")
    def send(self, pane, text):
        self.sent.append((pane, text))


class _Runtime:
    def shows_ready_ui(self, screen):
        return "shift+tab to cycle" in screen


def test_the_cycle_pulls_the_workspace_before_the_prompt(tmp_path):
    pulled = []
    reg = _Reg([Agent(name="g", role="worker", pane="p-g", workspace="/ws/g")])
    panes = _Panes({"p-g": IDLE_SAT})
    d = CycleDriver(tmp_path, reg, panes,
                    wiring=lambda a: LiveWiring(directions={"send"},
                                                settings_path="/s.json"),
                    refresh=lambda ws: pulled.append(ws) or None)
    assert d.sweep(reg.all(), _Runtime()) == ["g"]
    assert pulled == ["/ws/g"], "pull must happen for the prompted agent"


def test_a_refused_cycle_pull_is_loud_and_does_not_block_the_cycle(tmp_path):
    logs = []
    reg = _Reg([Agent(name="g", role="worker", pane="p-g", workspace="/ws/g")])
    panes = _Panes({"p-g": IDLE_SAT})
    d = CycleDriver(tmp_path, reg, panes,
                    wiring=lambda a: LiveWiring(directions={"send"},
                                                settings_path="/s.json"),
                    refresh=lambda ws: "fatal: Not possible to fast-forward",
                    log=logs.append)
    assert d.sweep(reg.all(), _Runtime()) == ["g"], "the /clear matters more"
    assert any("NOT brought current" in m for m in logs)


def test_a_dark_agent_gets_no_pull_and_no_prompt(tmp_path):
    pulled = []
    reg = _Reg([Agent(name="g", role="worker", pane="p-g", workspace="/ws/g")])
    panes = _Panes({"p-g": IDLE_SAT})
    d = CycleDriver(tmp_path, reg, panes, wiring=lambda a: None,
                    refresh=lambda ws: pulled.append(ws) or None)
    assert d.sweep(reg.all(), _Runtime()) == []
    assert pulled == [], "not st's agent -> not st's pull either"


# --- the bd repo default: one resolver, never the ambient cwd (internal-ref) ---

def test_the_beads_tracker_defaults_its_repo_via_the_feed_check_resolver(tmp_path, monkeypatch):
    """st inbox read 'no beads database found' from any non-store cwd — the
    tend-loop disease (bd5f55a) on the read side. The tracker's repo now
    defaults through feed_check.bd_cwd (admin workspace walked to .beads), so
    the Rule Zero gate and every beads-backed read share ONE answer."""
    from types import SimpleNamespace
    rig = tmp_path / "rig"
    ws = rig / "crew" / "sattler"
    ws.mkdir(parents=True)
    (rig / ".beads").mkdir()
    reg = _Reg([Agent(name="sattler", role="administrator", workspace=str(ws))])
    monkeypatch.setattr(cli, "_registry", lambda a: reg)
    a = SimpleNamespace(root=tmp_path, backend="beads", repo=None, registry="files")
    trk = cli._tracker(a)
    assert trk.repo == str(rig), "must resolve the rig root, not inherit the cwd"


def test_an_explicit_repo_flag_always_wins(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(cli, "_registry",
                        lambda a: (_ for _ in ()).throw(AssertionError("must not resolve")))
    a = SimpleNamespace(root=tmp_path, backend="beads", repo="/explicit/rig",
                        registry="files")
    assert cli._tracker(a).repo == "/explicit/rig"


def test_a_failed_resolution_falls_back_to_ambient_never_invents(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(cli, "_registry",
                        lambda a: (_ for _ in ()).throw(RuntimeError("no registry")))
    a = SimpleNamespace(root=tmp_path, backend="beads", repo=None, registry="files")
    assert cli._tracker(a).repo is None, "fail toward today's default, not a guess"
