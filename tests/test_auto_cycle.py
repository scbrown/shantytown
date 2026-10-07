"""tend PERFORMS the cycle for a deep, checkpointed, idle agent (aegis-zl7jwm).

Steve 2026-10-07: "actually cycle agents when context is deep." Each arm below
is one condition the AutoCycler must hold before it requests a cycle. A cycle
destroys whatever was not saved, so every refusal is pinned as well as the
acceptance.
"""
import json

from shantytown import cycle as cycle_mod
from shantytown.notify import AutoCycler, auto_cycle_enabled
from shantytown.protocols import Agent

from test_cycle import BUSY, IDLE, _Runtime, _saturated_pane


class _Panes:
    def __init__(self, screens):
        self.screens = dict(screens)
        self.sent = []

    def exists(self, pane):
        return pane in self.screens

    def capture(self, pane, history=0, attrs=False):
        return self.screens[pane]


class _Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _iso(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


AGENTS = [Agent(name="kelly", role="worker", pane="p-kelly")]


def _world(tmp_path, screen, *, prompted="saturated", comments=None,
           anchor="aegis-plate", clock=None):
    root = tmp_path
    (root / "notify").mkdir(exist_ok=True)
    (root / "notify" / "cycling.json").write_text(
        json.dumps({"kelly": prompted} if prompted else {}))
    panes = _Panes({"p-kelly": screen})
    reqs = cycle_mod.Requests(root)
    pushed = []
    store = {"comments": list(comments or [])}

    def push(reg, p, agent, msg):
        pushed.append((agent, msg))
        return agent

    def get_comments(bead):
        if isinstance(store["comments"], Exception):
            raise store["comments"]
        return store["comments"]

    clock = clock or _Clock()
    ac = AutoCycler(root, panes, anchor=lambda w: anchor,
                    comments=get_comments, requests=reqs, push=push, now=clock)
    return ac, panes, reqs, pushed, store, clock


def _sweep(ac):
    return ac.sweep(AGENTS, _Runtime())


def test_checkpointed_saturated_agent_gets_an_auto_request_on_the_second_sweep(tmp_path):
    ac, panes, reqs, pushed, store, clock = _world(tmp_path, _saturated_pane(646.0))
    assert _sweep(ac) == [], "first sight starts the clock; the agent gets its turn"
    clock.t += 300
    store["comments"] = [{"author": "kelly", "created_at": _iso(clock.t - 60)}]
    assert _sweep(ac) == ["kelly"]
    rec = reqs.pending()["kelly"]
    assert rec["auto"] is True and rec["checkpoint_bead"] == "aegis-plate"
    assert "646k" in rec["checkpoint"]


def test_a_comment_from_BEFORE_the_prompt_is_not_a_checkpoint(tmp_path):
    clock = _Clock()
    ac, panes, reqs, pushed, store, _ = _world(
        tmp_path, _saturated_pane(646.0), clock=clock,
        comments=[{"author": "kelly", "created_at": _iso(clock.t - 3600)}])
    _sweep(ac)
    clock.t += 300
    assert _sweep(ac) == []
    assert reqs.pending() == {}
    assert len(pushed) == 1 and "aegis-plate" in pushed[0][1], \
        "the gate refusing must nudge the agent to write one, never skip silently"


def test_the_checkpoint_nudge_goes_once_per_episode(tmp_path):
    ac, panes, reqs, pushed, store, clock = _world(tmp_path, _saturated_pane(646.0))
    for _ in range(4):
        _sweep(ac)
        clock.t += 300
    assert len(pushed) == 1


def test_someone_elses_comment_is_not_the_agents_checkpoint(tmp_path):
    ac, panes, reqs, pushed, store, clock = _world(tmp_path, _saturated_pane(646.0))
    _sweep(ac)
    clock.t += 300
    store["comments"] = [{"author": "sattler", "created_at": _iso(clock.t)}]
    assert _sweep(ac) == []


def test_a_busy_agent_is_never_requested(tmp_path):
    ac, panes, reqs, pushed, store, clock = _world(tmp_path, BUSY)
    store["comments"] = [{"author": "kelly", "created_at": _iso(clock.t + 10)}]
    for _ in range(3):
        _sweep(ac)
        clock.t += 300
    assert reqs.pending() == {} and pushed == []


def test_an_agent_the_driver_has_not_prompted_or_holds_dark_is_left_alone(tmp_path):
    for ledger in (None, "dark", "prehandoff"):
        ac, panes, reqs, pushed, store, clock = _world(
            tmp_path, _saturated_pane(646.0), prompted=ledger)
        store["comments"] = [{"author": "kelly", "created_at": _iso(clock.t + 10)}]
        _sweep(ac)
        clock.t += 300
        assert _sweep(ac) == [], ledger
        (tmp_path / "notify" / "auto-cycle.json").unlink(missing_ok=True)


def test_unreadable_comments_never_cycle(tmp_path):
    ac, panes, reqs, pushed, store, clock = _world(tmp_path, _saturated_pane(646.0))
    _sweep(ac)
    clock.t += 300
    store["comments"] = RuntimeError("store down")
    assert _sweep(ac) == [] and reqs.pending() == {} and pushed == []


def test_no_plate_bead_never_cycles(tmp_path):
    ac, panes, reqs, pushed, store, clock = _world(
        tmp_path, _saturated_pane(646.0), anchor="")
    _sweep(ac)
    clock.t += 300
    assert _sweep(ac) == [] and reqs.pending() == {}


def test_dropping_under_the_line_withdraws_the_auto_request_and_rearms(tmp_path):
    ac, panes, reqs, pushed, store, clock = _world(tmp_path, _saturated_pane(646.0))
    _sweep(ac)
    clock.t += 300
    store["comments"] = [{"author": "kelly", "created_at": _iso(clock.t)}]
    assert _sweep(ac) == ["kelly"]
    panes.screens["p-kelly"] = _saturated_pane(40.0)       # it cycled itself
    _sweep(ac)
    assert reqs.pending() == {}, "a self-cleared agent must not be cycled twice"
    assert json.loads((tmp_path / "notify" / "auto-cycle.json").read_text()) == {}


def test_an_agents_own_request_is_never_withdrawn(tmp_path):
    ac, panes, reqs, pushed, store, clock = _world(tmp_path, _saturated_pane(646.0))
    _sweep(ac)
    reqs.request("kelly", "my own checkpoint")
    panes.screens["p-kelly"] = _saturated_pane(40.0)
    _sweep(ac)
    assert "kelly" in reqs.pending()


def test_unreadable_depth_keeps_the_episode(tmp_path):
    ac, panes, reqs, pushed, store, clock = _world(tmp_path, _saturated_pane(646.0))
    _sweep(ac)
    panes.screens["p-kelly"] = BUSY          # depth unreadable while busy
    _sweep(ac)
    assert "kelly" in json.loads((tmp_path / "notify" / "auto-cycle.json").read_text())


def test_the_kill_switch(monkeypatch):
    for off in ("0", "off", "FALSE", "no"):
        monkeypatch.setenv("SHANTY_AUTO_CYCLE", off)
        assert not auto_cycle_enabled()
    monkeypatch.delenv("SHANTY_AUTO_CYCLE")
    assert auto_cycle_enabled()


# --- tend serves an auto request only at a turn boundary --------------------

import pytest  # noqa: E402

from shantytown import cli  # noqa: E402


def _tend(tmp_path, monkeypatch, screen):
    from tests.test_tend import _Args, _Panes as _TPanes, _roster
    _roster(tmp_path, {"worker": {"role": "worker", "pane": "p-worker"}})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _TPanes(
        screens={"p-worker": screen}, live={"p-worker"}))
    seen = []
    monkeypatch.setattr(cli, "_cmd_cycle", lambda ns: seen.append(ns) or cli.OK)
    cli._tend_once(_Args(tmp_path, backend="files"))
    return [ns.agent for ns in seen]


@pytest.mark.parametrize("screen,served", [
    (_saturated_pane(646.0), True), (IDLE, True), (BUSY, False)])
def test_tend_serves_an_auto_request_only_while_idle(tmp_path, monkeypatch,
                                                     screen, served):
    cycle_mod.Requests(tmp_path).request("worker", "auto", auto=True)
    assert ("worker" in _tend(tmp_path, monkeypatch, screen)) is served


def test_tend_still_serves_an_agents_own_request_while_busy(tmp_path, monkeypatch):
    """The agent asked; its own request keeps today's behaviour."""
    cycle_mod.Requests(tmp_path).request("worker", "mine")
    assert "worker" in _tend(tmp_path, monkeypatch, BUSY)
