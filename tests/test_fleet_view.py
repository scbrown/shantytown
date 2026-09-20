"""Peer visibility fails loudly; each peer classifies its own pane contents."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from shantytown import cli, fleet
from shantytown.config import HostPeer
from test_crew_work import _Args, _Panes, _roster, IDLE_SCREEN, BUSY_SCREEN


def row(name='remote', work='busy'):
    return dict(name=name, host='laptop', role='worker', state='up', work=work,
                posture='auto', harness='codex', live=True, pane='remote-pane',
                settings='current', tree='current')


def snapshot(**overrides):
    return dict(version=1, scope='local', complete=True, host='laptop',
                agents=[row()], **overrides)


def test_peer_command_is_bounded_quoted_and_nonrecursive(monkeypatch):
    def run(argv, **kw):
        assert argv[:7] == ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '--', 'user@example.com']
        assert "--root '/tmp/a b' crew --json --local" in argv[-1]
        assert kw['timeout'] == 20
        return SimpleNamespace(returncode=0, stdout=json.dumps(snapshot()), stderr='')
    monkeypatch.setattr(fleet.subprocess, 'run', run)
    result = fleet.read_peer(HostPeer('laptop', 'user@example.com', '/tmp/a b'))
    assert result['error'] is None
    assert result['agents'][0]['work'] == 'busy'


@pytest.mark.parametrize('payload', [None, {}, {'version': 99},
    dict(snapshot(), scope='fleet'), dict(snapshot(), host='wrong'),
    dict(snapshot(), complete=False), dict(snapshot(), agents=[{}]),
    dict(snapshot(), agents=[row(), row()]), dict(snapshot(), agents=[dict(row(), live=1)])])
def test_invalid_snapshot_is_unreachable(monkeypatch, payload):
    monkeypatch.setattr(fleet.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=''))
    result = fleet.read_peer(HostPeer('laptop', 'user@example.com', '/tmp/root'))
    assert result['error']
    assert 'UNREACHABLE' in fleet.errors([result])[0]
    assert result['agents'] == []


@pytest.mark.parametrize('failure', [subprocess.TimeoutExpired('ssh', 20), OSError('no ssh')])
def test_transport_failure_is_not_zero_agents(monkeypatch, failure):
    def run(*a, **k):
        raise failure
    monkeypatch.setattr(fleet.subprocess, 'run', run)
    assert fleet.read_peer(HostPeer('laptop', 'user@example.com', '/tmp/root'))['error']


def setup(tmp_path, monkeypatch):
    root = _roster(tmp_path, {'local': 'local-pane'})
    (root / 'shantytown.toml').write_text(
        '[host]\nname="desktop"\n[host.peers.laptop]\nssh="user@example.com"\nroot="/tmp/root"\n')
    monkeypatch.setattr(cli, 'Tmux', lambda *a, **k: _Panes({'local-pane': IDLE_SCREEN}))
    monkeypatch.setattr(fleet, 'collect', lambda peers:
                        [dict(host='laptop', agents=[row()], error=None)])
    return _Args(root)


def test_table_merges_hosts_and_retains_local_diagnostics(tmp_path, monkeypatch, capsys):
    args = setup(tmp_path, monkeypatch)
    assert cli._cmd_crew(args) == 0
    out = capsys.readouterr().out
    assert 'HOST' in out
    assert 'desktop' in out and 'laptop' in out
    assert 'remote' in out and 'busy' in out
    assert 'Local diagnostics:' in out and '1 free: local' in out


def test_local_json_is_readiness_content_and_does_not_poll(tmp_path, monkeypatch, capsys):
    args = setup(tmp_path, monkeypatch)
    args.local = args.json = True
    monkeypatch.setattr(fleet, 'collect', lambda _: pytest.fail('recursive polling'))
    assert cli._cmd_crew(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['scope'] == 'local' and out['host'] == 'desktop'
    assert len(out['agents']) == 1 and out['agents'][0]['work'] == 'idle'
    monkeypatch.setattr(cli, 'Tmux', lambda *a, **k: _Panes({'local-pane': BUSY_SCREEN}))
    assert cli._cmd_crew(args) == 0
    assert json.loads(capsys.readouterr().out)['agents'][0]['work'] == 'busy'


def test_count_includes_remote_content(tmp_path, monkeypatch, capsys):
    args = setup(tmp_path, monkeypatch)
    args.count = True
    assert cli._cmd_crew(args) == 0
    assert capsys.readouterr().out.strip() == '1/2'


@pytest.mark.parametrize('mode', ['table', 'json', 'count', 'governor'])
def test_unreachable_remains_visible_and_nonzero(tmp_path, monkeypatch, capsys, mode):
    from shantytown import fleet_governor
    args = setup(tmp_path, monkeypatch)
    if mode != 'table':
        setattr(args, mode, True)
    monkeypatch.setattr(fleet, 'collect', lambda peers:
                        [dict(host='laptop', agents=[], error='deadline')])
    monkeypatch.setattr(fleet_governor, 'collect', lambda peers:
                        [(None, 'laptop UNREACHABLE (deadline)')])
    assert cli._cmd_crew(args) == cli.CANNOT_TELL
    out = capsys.readouterr().out
    assert 'laptop UNREACHABLE' in out
    if mode == 'json':
        assert json.loads(out)['complete'] is False
    if mode == 'count':
        assert out.startswith('unknown ')
    if mode == 'governor':
        assert out.startswith('lost ')


def test_local_switch_preserves_old_table(tmp_path, monkeypatch, capsys):
    args = setup(tmp_path, monkeypatch)
    args.local = True
    assert cli._cmd_crew(args) == 0
    out = capsys.readouterr().out
    assert 'HOST' not in out and 'remote' not in out


def test_live_counts_keep_provider_lanes():
    remote = [dict(agents=[row(), dict(row('second'), harness='claude'),
                           dict(row('down'), live=False)])]
    assert fleet.live_counts(remote, {'base': None, 'codex': None}) == {'base': 1, 'codex': 1}


def test_remote_ungoverned_provider_is_not_charged_to_base():
    assert fleet.live_counts([dict(agents=[dict(row(), harness='other')])],
                             {'base': None}) == {'base': 0}


def test_governor_display_counts_local_and_remote(tmp_path, monkeypatch, capsys):
    from shantytown import fleet_governor
    from test_fleet_governor import sample, agent
    args = setup(tmp_path, monkeypatch)
    args.governor = True
    monkeypatch.setattr(fleet_governor, 'collect', lambda peers: [(
        sample('laptop', agents=[agent('laptop', 'remote')]), '')])
    assert cli._cmd_crew(args) == 0
    assert 'live 2/2' in capsys.readouterr().out


def test_failed_peer_does_not_hide_another_peer(monkeypatch):
    peers = {name: HostPeer(name, 'user@example.com', '/tmp/root')
             for name in ('laptop', 'other')}
    monkeypatch.setattr(fleet, 'read_peer', lambda p: dict(
        host=p.name, agents=[row()] if p.name == 'laptop' else [],
        error=None if p.name == 'laptop' else 'timeout'))
    results = fleet.collect(peers)
    assert len(fleet.rows(results)) == 1
    assert fleet.errors(results) == ['other UNREACHABLE (timeout)']


def test_remote_agents_visible_with_empty_local_roster(tmp_path, monkeypatch, capsys):
    args = setup(tmp_path, monkeypatch)
    (tmp_path / 'crew' / 'local.json').unlink()
    assert cli._cmd_crew(args) == 0
    out = capsys.readouterr().out
    assert 'laptop' in out and 'remote' in out
    assert 'no agents.' not in out
