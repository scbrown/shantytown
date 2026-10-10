"""A dim Codex prompt is empty input; unfamiliar ghosts still refuse cycles."""
import argparse

import pytest

from shantytown import cli, cycle, triage
from shantytown.protocols import Agent
from shantytown.runtime import ClaudeRuntime

PROMPT = '\x1b[1m›\x1b[0m \x1b[2mAsk Codex to do anything\x1b[0m'


def screen(prompt=PROMPT, footer=''):
    return f'{prompt}\n{footer}\n? for shortcuts\n'


class Panes:
    def __init__(self, painted):
        self.painted = painted

    def exists(self, pane):
        return True

    def capture(self, pane, history=0, attrs=False):
        assert attrs, 'cycle must preserve the distinguishing dim attribute'
        return self.painted


def card():
    return Agent(name='worker', role='worker', pane='p-worker', harness='codex')


def refusal(painted):
    panes = Panes(painted)
    runtime = ClaudeRuntime(panes, lambda *a: None)
    return cli._automatic_cycle_refusal(card(), 'p-worker', panes, runtime)


def test_known_dim_placeholder_passes_input_gate_but_retains_shell_visibility_gate():
    assert triage.input_state(screen()) == triage.INPUT_PLACEHOLDER
    assert 'zero is not visible' in refusal(screen())


def test_known_dim_placeholder_with_visible_zero_is_accepted():
    assert refusal(screen(footer='· 0 shells ·')) == ''


@pytest.mark.parametrize('prompt', [
    '› Ask Codex to do anything',  # stripped attributes cannot prove dim
    '\x1b[1m›\x1b[0m Ask Codex to do anything',  # identical typed words
    '\x1b[1m›\x1b[0m typed \x1b[2mAsk Codex to do anything\x1b[0m',
    '\x1b[1m›\x1b[0m \x1b[2mAsk Codex to do anything\x1b[0m typed',
    '\x1b[1m›\x1b[0m \x1b[2mExplain this code\x1b[0m',  # unfamiliar ghost
])
def test_unsafe_composers_refuse(prompt):
    assert refusal(screen(prompt, '· 0 shells ·'))


def test_placeholder_does_not_hide_background_work():
    assert 'background shell' in refusal(screen(footer='· 1 shell ·'))
    assert refusal(screen(footer='Waiting for background terminal (esc to interrupt)'))


def test_placeholder_is_harness_specific():
    claude = Agent(name='worker', role='worker', pane='p-worker', harness='claude')
    panes = Panes(screen())
    assert cli._automatic_cycle_refusal(
        claude, 'p-worker', panes, ClaudeRuntime(panes, lambda *a: None))


def test_plan_and_locked_execution_share_the_input_rule(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, '_foreign_session_refusal', lambda *a, **k: None)
    monkeypatch.setattr(cli.harness_mod, 'clear_command_for', lambda *a: '/clear')
    panes = Panes(screen(footer='· 0 shells ·'))
    runtime = ClaudeRuntime(panes, lambda *a: None)
    args = argparse.Namespace(root=str(tmp_path), no_in_place=False)
    assert cli._cycle_plan(args, card(), 'p-worker', panes, runtime).mode == cycle.SOFT
    assert cli._automatic_cycle_refusal(card(), 'p-worker', panes, runtime, tmp_path) == ''
