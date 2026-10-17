"""A feed hold never becomes an instruction to stop or block explicit work."""
from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from shantytown import agent_hold, cli, feed_check, feed_hold, stop_event
from shantytown.tmux import NullPanes
from test_go_cli import _root


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def test_cli_hold_and_clear_leave_pane_live_and_explicit_dispatch_works(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path)
    note = tmp_path / 'reason'
    note.write_text('coordinator duty')
    panes = NullPanes(screen='', live={'%5'}, owned={'%5'})
    monkeypatch.setattr(cli, 'Tmux', lambda *a, **k: panes)
    monkeypatch.setattr(panes, 'kill_session', lambda *a: pytest.fail('stopped live agent'))
    common = ['--root', str(root), '--backend', 'files']
    assert cli.main([*common, 'work', 'hold-feed', 'ellie', '--until', '2099-01-01',
                     '--reason-file', str(note)]) == 0
    assert panes.exists('%5')
    assert 'feed held' in feed_check.haul_hold_reason(root, 'ellie')
    assert not agent_hold.reason(root, 'ellie')
    assert not agent_hold.finish_turn(root, 'ellie')
    # The hold is not a gaming/account/explicit-dispatch waiver or a dispatch block.
    assert cli.main([*common, 'go', 'item-1', 'ellie']) == 0
    assert json.loads((root / 'items/item-1.json').read_text())['status'] == 'in_progress'
    assert cli.main([*common, 'work', 'hold-feed', 'ellie', '--status']) == 0
    assert 'feed held' in capsys.readouterr().out
    assert cli.main([*common, 'work', 'hold-feed', 'ellie', '--clear']) == 0
    assert not feed_hold.reason(root, 'ellie')
    assert panes.exists('%5')


def test_hold_never_invokes_after_turn_stop_or_mutates_card(tmp_path, monkeypatch):
    root = _root(tmp_path)
    before = (root / 'crew/ellie.json').read_bytes()
    popen = Mock(side_effect=AssertionError('spawned lifecycle process'))
    monkeypatch.setattr(agent_hold.subprocess, 'Popen', popen)
    feed_hold.hold(root, 'ellie', 'lead', '2099-01-01', 'quiet duty')
    assert not agent_hold.finish_turn(root, 'ellie')
    assert (root / 'crew/ellie.json').read_bytes() == before
    popen.assert_not_called()


def test_stop_hook_holds_before_board_reads_and_claims(tmp_path, monkeypatch, capsys):
    from test_haul import _Reg, _Panes, WORKER
    reg = _Reg([WORKER])
    panes = _Panes({'p-b': ''})
    feed_hold.hold(tmp_path, 'billy', 'lead', '2099-01-01', 'available by inbox')
    monkeypatch.setattr(stop_event, '_bd_json', lambda *a, **k: pytest.fail('read or claimed board'))
    assert stop_event._haul(reg, panes, 'billy', tmp_path) == 0
    assert 'feed held' in capsys.readouterr().err
    assert panes.exists('p-b')


def test_idle_tend_and_codex_resume_skip_only_held_agent(tmp_path, monkeypatch):
    from shantytown.files import FilesRegistry
    from shantytown.protocols import Agent
    reg = FilesRegistry(tmp_path / 'crew')
    reg.set(Agent(name='worker', role='worker', pane='p', harness='codex'))
    panes = NullPanes(live={'p'})
    feed_hold.hold(tmp_path, 'worker', 'lead', '2099-01-01', 'coordinator')
    runtime = Mock()
    assert feed_check.free_feedable_workers(reg, panes, runtime, tmp_path) == []
    assert feed_check.idle_resumable_codex(
        reg, panes, runtime, [{'assignee': 'worker'}], tmp_path) == []
    runtime.assert_not_called()
    assert feed_hold.reason(tmp_path, 'other') == ''


def test_date_auto_expiry_retains_provenance_and_replacement_wins(tmp_path):
    feed_hold.hold(tmp_path, 'worker', 'lead', stamp(200), 'first', now=100)
    path = tmp_path / 'feed-holds/worker.json'
    before = path.read_bytes()
    assert 'feed held' in feed_hold.reason(tmp_path, 'worker', now=199)
    assert feed_hold.reason(tmp_path, 'worker', now=200) == ''
    assert path.read_bytes() == before
    feed_hold.hold(tmp_path, 'worker', 'lead', stamp(400), 'replacement', now=201)
    assert 'replacement' in feed_hold.reason(tmp_path, 'worker', now=202)


@pytest.mark.parametrize('answer,active', [(False, True), (True, False), (None, True)])
def test_closed_resume_is_exact_and_unknown_keeps_hold(tmp_path, answer, active):
    feed_hold.hold(tmp_path, 'worker', 'lead', 'closed:project-123', 'dependency')
    ids = []
    def closed(identifier):
        ids.append(identifier)
        return answer
    assert bool(feed_hold.reason(tmp_path, 'worker', closed=closed)) is active
    assert ids == ['project-123']


def test_failed_resume_read_cannot_lift_hold(tmp_path):
    feed_hold.hold(tmp_path, 'worker', 'lead', 'closed:project-123', 'dependency')
    def failed(identifier):
        raise TimeoutError
    assert 'UNKNOWN' in feed_hold.reason(tmp_path, 'worker', closed=failed)


def test_closed_condition_uses_selected_deployment_exact_reader(tmp_path):
    root = _root(tmp_path)
    (root / 'shantytown.toml').write_text('[env]\nSHANTY_BACKEND="files"\n')
    feed_hold.hold(root, 'ellie', 'lead', 'closed:item-1', 'await exact result')
    assert feed_hold.reason(root, 'ellie')
    path = root / 'items/item-1.json'
    row = json.loads(path.read_text())
    row['status'] = 'closed'
    path.write_text(json.dumps(row))
    assert feed_hold.reason(root, 'ellie') == ''


@pytest.mark.parametrize('until', ['2099-01-01T12:00:00', 'tomorrow', 'closed:',
                                   'closed:../item', '2000-01-01'])
def test_invalid_or_past_expiry_refuses_without_record(tmp_path, until):
    with pytest.raises(ValueError):
        feed_hold.hold(tmp_path, 'worker', 'lead', until, 'reason')
    assert not (tmp_path / 'feed-holds/worker.json').exists()


def test_corrupt_or_symlink_record_holds_and_clear_is_explicit(tmp_path):
    path = tmp_path / 'feed-holds/worker.json'
    path.parent.mkdir()
    path.write_text('broken')
    assert 'unreadable' in feed_hold.reason(tmp_path, 'worker')
    feed_hold.clear(tmp_path, 'worker')
    target = tmp_path / 'other'
    target.write_text('preserve')
    path.symlink_to(target)
    assert 'unreadable' in feed_hold.reason(tmp_path, 'worker')
    with pytest.raises(ValueError):
        feed_hold.hold(tmp_path, 'worker', 'lead', '2099-01-01', 'reason')
    feed_hold.clear(tmp_path, 'worker')
    assert target.read_text() == 'preserve'


def test_dry_run_and_missing_reason_do_not_write(tmp_path):
    root = _root(tmp_path)
    note = tmp_path / 'reason'
    note.write_text('stay reachable')
    common = ['--root', str(root), '--backend', 'files', 'work', 'hold-feed', 'ellie']
    assert cli.main([*common, '--until', '2099-01-01', '--reason-file', str(note),
                     '--dry-run']) == 0
    assert not (root / 'feed-holds').exists()
    assert cli.main([*common, '--until', '2099-01-01']) == cli.REFUSED
    assert not (root / 'feed-holds').exists()


def test_crew_and_anchor_report_feed_hold_without_changing_pane_state(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path)
    panes = NullPanes(live={'%5'}, screen='> \n? for shortcuts')
    monkeypatch.setattr(cli, 'Tmux', lambda *a, **k: panes)
    feed_hold.hold(root, 'ellie', 'lead', '2099-01-01', 'coordinator duty')
    common = ['--root', str(root), '--backend', 'files']
    assert cli.main([*common, 'anchor', 'ellie']) == 0
    assert 'ellie: feed held' in capsys.readouterr().out
    assert cli.main([*common, 'crew', '--local']) == 0
    assert 'ellie: feed held' in capsys.readouterr().out
    assert panes.exists('%5')


@pytest.mark.parametrize('local', [None, 'other'])
def test_offhost_or_unknown_placement_refuses_before_writing(tmp_path, monkeypatch, local):
    from dataclasses import replace
    from shantytown import deployment
    from shantytown.files import FilesRegistry
    root = _root(tmp_path)
    reg = FilesRegistry(root / 'crew')
    reg.set(replace(reg.get('ellie'), host='target'))
    monkeypatch.setattr(deployment, 'local_host', lambda root: local)
    note = tmp_path / 'reason'
    note.write_text('coordinate')
    assert cli.main(['--root', str(root), '--backend', 'files', 'work', 'hold-feed',
                     'ellie', '--until', '2099-01-01', '--reason-file', str(note)]) == cli.REFUSED
    assert not (root / 'feed-holds').exists()


def test_unreadable_registry_refuses_without_hold_write(tmp_path, monkeypatch):
    from shantytown.quipu import QuipuUnreachable
    root = _root(tmp_path)
    def unavailable(a):
        raise QuipuUnreachable('fixture registry unavailable')
    monkeypatch.setattr(cli, '_registry', unavailable)
    assert cli.main(['--root', str(root), '--backend', 'files', 'work', 'hold-feed',
                     'ellie', '--status']) == cli.REFUSED
    assert not (root / 'feed-holds').exists()
