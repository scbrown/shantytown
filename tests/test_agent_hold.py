"""An operator's after-turn intent gates feeds before the pane is stopped."""
from types import SimpleNamespace
from unittest.mock import Mock

from shantytown import agent_hold, cli, feed_check, stop_event
from shantytown.files import FilesRegistry
from shantytown.protocols import Agent
from shantytown.tmux import NullPanes


def world(tmp_path, monkeypatch):
    reg = FilesRegistry(tmp_path / 'crew')
    reg.set(Agent(name='worker', role='worker', pane='crew-worker'))
    panes = NullPanes(live={'crew-worker'}, owned={'crew-worker'})
    monkeypatch.setattr(cli, '_registry', lambda a: reg)
    monkeypatch.setattr(cli, '_panes', lambda a: panes)
    return reg, panes


def args(root, **kwargs):
    return SimpleNamespace(root=root, agent='worker', dry_run=False,
                           after_turn=True, reason='budget hold', **kwargs)


def test_hold_preserves_pane_and_blocks_feed_and_dispatch(tmp_path, monkeypatch):
    reg, panes = world(tmp_path, monkeypatch)
    assert not feed_check.haul_hold_reason(tmp_path, 'worker')
    assert cli._cmd_stop(args(tmp_path)) == 0
    assert panes.exists('crew-worker')
    assert 'held (by' in feed_check.haul_hold_reason(tmp_path, 'worker')
    assert cli._cmd_go(args(tmp_path)) == cli.REFUSED
    runtime = Mock()
    assert feed_check.free_feedable_workers(reg, panes, runtime, tmp_path) == []
    cli._launched_now(args(tmp_path), 'worker')
    assert not feed_check.haul_hold_reason(tmp_path, 'worker')


def test_dry_run_does_not_hold_and_foreign_pane_refused(tmp_path, monkeypatch):
    _, panes = world(tmp_path, monkeypatch)
    a = args(tmp_path)
    a.dry_run = True
    assert cli._cmd_stop(a) == 0
    assert not agent_hold.reason(tmp_path, 'worker')
    a.dry_run = False
    monkeypatch.setattr(panes, 'owns', lambda p: False)
    assert cli._cmd_stop(a) == cli.REFUSED
    assert not agent_hold.reason(tmp_path, 'worker')


def test_hold_is_durable_and_stop_boundary_spawns_guarded_detached_stop(tmp_path, monkeypatch):
    agent_hold.hold(tmp_path, 'worker', 'lead', 'budget')
    popen = Mock()
    monkeypatch.setattr(agent_hold.subprocess, 'Popen', popen)
    assert agent_hold.finish_turn(tmp_path, 'worker')
    call = popen.call_args
    assert call.args[0][-5:-2] == ['agent', 'stop', 'worker']
    assert call.kwargs['start_new_session'] is True
    assert agent_hold.reason(tmp_path, 'worker').startswith('held (by lead, since ')
    agent_hold.clear(tmp_path, 'worker')
    popen.reset_mock()
    assert not agent_hold.finish_turn(tmp_path, 'worker')
    popen.assert_not_called()


def test_send_boundary_persists_event_before_stop(tmp_path, monkeypatch):
    world(tmp_path, monkeypatch)
    monkeypatch.setenv('SHANTY_AGENT', 'worker')
    monkeypatch.setattr(stop_event, '_stop_identity', lambda *a: 'worker')
    calls = []
    monkeypatch.setattr(stop_event, '_send', lambda *a: calls.append('event') or 0)
    monkeypatch.setattr(agent_hold, 'finish_turn', lambda *a: calls.append('stop'))
    assert stop_event.main(['send', '--root', str(tmp_path)]) == 0
    assert calls == ['event', 'stop']


def test_unreadable_hold_cannot_feed(tmp_path):
    path = tmp_path / 'agent-holds' / 'worker.json'
    path.parent.mkdir()
    path.write_text('broken')
    assert 'unreadable' in feed_check.haul_hold_reason(tmp_path, 'worker')
    agent_hold.clear(tmp_path, 'worker')
    assert not feed_check.haul_hold_reason(tmp_path, 'worker')


def test_boundary_executes_existing_stop_bookkeeping(tmp_path, monkeypatch):
    _, panes = world(tmp_path, monkeypatch)
    agent_hold.hold(tmp_path, 'worker', 'lead')
    def detached_stop(argv, **kwargs):
        a = args(tmp_path)
        a.after_turn = False
        assert cli._cmd_stop(a) == 0
        return Mock(pid=42)
    monkeypatch.setattr(agent_hold.subprocess, 'Popen', detached_stop)
    assert agent_hold.finish_turn(tmp_path, 'worker')
    assert not panes.exists('crew-worker')
    assert cli._stops(args(tmp_path)).get('worker') is not None
    assert agent_hold.reason(tmp_path, 'worker')
