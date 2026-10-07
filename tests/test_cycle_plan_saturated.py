"""A saturated agent is IDLE for the purpose of choosing the cycle mechanism.

work_state derives SATURATED in its IDLE branch: the pane is up, quiet, and its
input box is empty, and the only difference is that the depth is past the cycle
line. That is exactly the agent a cycle exists for. Reading it as "not idle" made
_cycle_plan pick RESPAWN with the reason "the agent is mid-turn" for every
saturated agent, replacing the process when a typed clear was safe, and printing
a reason that was false.
"""
import argparse

from shantytown import cli, cycle as cycle_mod
from shantytown.protocols import Agent

from test_cycle import BUSY, IDLE, _Runtime, _saturated_pane


class _Panes:
    def __init__(self, screen):
        self._screen = screen

    def exists(self, pane):
        return True

    def capture(self, pane, history=0, attrs=False):
        return self._screen


def _plan(tmp_path, monkeypatch, screen):
    monkeypatch.setattr(cli, "_foreign_session_refusal", lambda *a, **k: None)
    card = Agent(name="ellie", role="worker", pane="shanty-ellie")
    a = argparse.Namespace(root=str(tmp_path), no_in_place=False)
    return cli._cycle_plan(a, card, "shanty-ellie", _Panes(screen), _Runtime())


def test_a_saturated_idle_pane_is_cleared_in_place(tmp_path, monkeypatch):
    chosen = _plan(tmp_path, monkeypatch, _saturated_pane(646.0))
    assert chosen.mode == cycle_mod.SOFT, chosen
    assert "mid-turn" not in chosen.why


def test_a_plain_idle_pane_is_still_cleared_in_place(tmp_path, monkeypatch):
    assert _plan(tmp_path, monkeypatch, IDLE).mode == cycle_mod.SOFT


def test_a_busy_pane_is_still_not_cleared_in_place(tmp_path, monkeypatch):
    chosen = _plan(tmp_path, monkeypatch, BUSY)
    assert chosen.mode != cycle_mod.SOFT
