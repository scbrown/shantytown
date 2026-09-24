"""Slow healthy daemon startup must not make a cycle destroy its session."""
from types import SimpleNamespace

import pytest

from shantytown import cli, codex_ready, cycle
from shantytown.protocols import Agent
from shantytown.tmux import NullPanes


def observer(monkeypatch, ready_at):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(cli, "time", SimpleNamespace(
        sleep=lambda seconds: setattr(clock, "now", clock.now + seconds)))
    panes = NullPanes(live={"fixture"})
    runtime = SimpleNamespace(is_live=lambda screen: clock.now >= ready_at)
    return clock, panes, runtime


@pytest.mark.parametrize("ready_at", [6.0, codex_ready.TIMEOUT + 1.0])
def test_slow_connect_keeps_cycle_session(monkeypatch, ready_at):
    clock, panes, runtime = observer(monkeypatch, ready_at)
    card = Agent(name="fixture", role="worker", harness="codex")
    launches = []

    def launch(a, card, panes, runtime, **kw):
        launches.append(kw)
        panes.respawn("fixture")
        return cli.OK if cli._observe_live(runtime, panes, "fixture", card) else cli.CANNOT_TELL

    monkeypatch.setattr(cli, "_launch", launch)
    monkeypatch.setattr(cli, "_foreign_session_refusal", lambda *a: None)
    monkeypatch.setattr(cli, "_capture_history_before_kill", lambda *a: None)
    # Crossing into either fallback gate is the actual incident, not just a
    # different timeout number. Fail before any fallback side effects.
    def fallback(*a, **kw):
        pytest.fail("healthy delayed launch entered destructive fallback")
    monkeypatch.setattr(cli, "_launch_admitted", fallback)
    monkeypatch.setattr(cli, "_cmd_stop", fallback)
    result = cli._restart_cycle(SimpleNamespace(), card, "fixture", "fixture",
                                panes, runtime, cycle.Plan(cycle.RESPAWN, "fixture"), None)
    assert result == (cli.OK, cycle.RESPAWN)
    assert launches == [{"reuse_session": True}]
    assert panes.exists("fixture")
    assert clock.now == ready_at


@pytest.mark.parametrize("harness,bound", [("codex", codex_ready.TIMEOUT + 5), ("claude", 5)])
def test_never_ready_stays_unknown_and_bounded(monkeypatch, harness, bound):
    clock, panes, runtime = observer(monkeypatch, float("inf"))
    card = Agent(name="fixture", role="worker", harness=harness)
    assert not cli._observe_live(runtime, panes, "fixture", card)
    assert bound <= clock.now <= bound + cli._LIVE_DELAY


def test_budget_follows_readiness_constant(monkeypatch):
    monkeypatch.setattr(codex_ready, "TIMEOUT", 30.0)
    clock, panes, runtime = observer(monkeypatch, 31.0)
    card = Agent(name="fixture", role="worker", harness="codex")
    assert cli._observe_live(runtime, panes, "fixture", card)
    assert clock.now == 31.0
