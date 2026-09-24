"""New-host acceptance must turn red for every missing prerequisite."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from shantytown import cli, deploy_doctor as doc


@pytest.fixture
def ready(tmp_path, monkeypatch):
    from shantytown import runtime
    import sys
    monkeypatch.setattr(runtime, "_hook_interpreter", lambda: sys.executable)
    root = tmp_path / 'second store'
    workspace = tmp_path / 'workspaces'
    assert cli.main(['--root', str(root), 'fleet', 'init', '-y', '--host', 'second',
                     '--admin', 'boss', '--crew', 'worker', '--workspaces', str(workspace),
                     '--peer', 'first=operator@first.example,/opt/first']) == 0
    for name in ('boss', 'worker'):
        (workspace / name).mkdir(parents=True)
    monkeypatch.setenv('SHANTY_ROOT', str(root))
    monkeypatch.setenv('SHANTY_BACKEND', 'files')
    monkeypatch.setattr(doc.relay_doctor.shutil, 'which', lambda n, **kw: '/bin/' + n)
    monkeypatch.setattr(doc, '_host_graph', lambda *a: doc._row('graph Host', 0, 'verified fixture'))
    return root


def rows(report):
    return {r['name']: r for r in report['checks']}


def test_fresh_setup_is_incomplete_until_workspaces_and_relay_exist(tmp_path, monkeypatch):
    root = tmp_path / 'new'
    assert cli.main(['--root', str(root), 'fleet', 'init', '-y', '--host', 'second',
                     '--crew', 'worker', '--workspaces', str(tmp_path / 'missing')]) == 0
    monkeypatch.setattr(doc, '_host_graph', lambda *a: doc._row('graph Host', 1, 'missing'))
    report = doc.check(root, local=True)
    assert doc.exit_code(report) == 1
    found = rows(report)
    for name in ('peers', 'incoming relay environment', 'graph Host', 'worker/workspace'):
        assert found[name]['code'] == 1


def test_ready_local_snapshot_is_read_only(ready):
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in ready.rglob('*') if p.is_file()}
    report = doc.check(ready, local=True)
    assert doc.exit_code(report) == 0
    assert report['scope'] == 'local'
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in ready.rglob('*') if p.is_file()}


@pytest.mark.parametrize('event', ['Stop', 'PostToolUse'])
def test_missing_capture_is_red(ready, event):
    path = ready / 'settings/worker.settings.json'
    data = json.loads(path.read_text())
    data['hooks'].pop(event)
    path.write_text(json.dumps(data))
    assert rows(doc.snapshot(ready))[f'worker/{event} capture']['code'] == 1


def test_selected_override_cannot_hide_behind_good_role(ready):
    (ready / 'settings/agent-worker.settings.json').write_text('{"hooks":{}}')
    report = doc.snapshot(ready)
    assert rows(report)['worker/Stop capture']['code'] == 1


@pytest.mark.parametrize('damage', ['missing', 'invalid'])
def test_missing_or_invalid_settings_is_not_green(ready, damage):
    path = ready / 'settings/worker.settings.json'
    if damage == 'missing':
        path.unlink()
    else:
        path.write_text('{')
    assert rows(doc.snapshot(ready))['worker/settings']['code'] == (1 if damage == 'missing' else 2)


def test_permissions_must_be_decided(ready):
    path = ready / 'crew/worker.json'
    data = json.loads(path.read_text())
    data['dangerous'] = None
    path.write_text(json.dumps(data))
    assert rows(doc.snapshot(ready))['worker/permissions']['code'] == 1


def test_incoming_environment_not_masked_by_config(ready, monkeypatch, capsys):
    with (ready / 'shantytown.toml').open('a') as f:
        f.write('\n[env]\nSHANTY_ROOT=' + json.dumps(str(ready)) + '\nSHANTY_BACKEND="files"\n')
    monkeypatch.delenv('SHANTY_ROOT')
    monkeypatch.delenv('SHANTY_BACKEND')
    assert cli.main(['--root', str(ready), 'ops', 'doctor', '--deploy', '--local', '--json']) == 1
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert rows(report)['incoming relay environment']['code'] == 1


def test_no_peer_recursion(ready, monkeypatch):
    monkeypatch.setattr(doc, '_read_peer', lambda _: pytest.fail('remote read'))
    assert doc.exit_code(doc.check(ready, local=True)) == 0


def peer_snapshot(root):
    return {'version': 1, 'scope': 'local', 'host': 'first', 'root': '/opt/first',
            'peers': {'second': {'ssh': 'operator@second.example', 'root': str(root)}},
            'checks': [doc._row('host', 0, 'first')]}


@pytest.mark.parametrize('fault', ['none', 'missing', 'wrong-root', 'unreachable', 'remote-incomplete'])
def test_reciprocal_peer_is_load_bearing(ready, monkeypatch, fault):
    other = peer_snapshot(ready)
    if fault == 'missing':
        other['peers'] = {}
    if fault == 'wrong-root':
        other['peers']['second']['root'] = '/wrong'
    if fault == 'remote-incomplete':
        other['checks'].append(doc._row('graph Host', 1, 'missing'))
    def read(_):
        if fault == 'unreachable':
            raise OSError('secret must not print')
        return other
    monkeypatch.setattr(doc, '_read_peer', read)
    report = doc.check(ready)
    assert doc.exit_code(report) == (0 if fault == 'none' else 2 if fault == 'unreachable' else 1)
    assert 'secret' not in doc.render(report)


@pytest.mark.parametrize('fault', ['none', 'bad-json', 'wrong-host', 'wrong-root', 'empty', 'bad-code', 'exit-mismatch'])
def test_peer_wire_contract_and_shell_quoting(monkeypatch, fault):
    from shantytown.config import HostPeer
    peer = HostPeer('first', 'operator@first.example', "/opt/store with 'quote")
    other = peer_snapshot('/second')
    other['root'] = peer.root
    if fault == 'wrong-host': other['host'] = 'stranger'
    if fault == 'wrong-root': other['root'] = '/elsewhere'
    if fault == 'empty': other['checks'] = []
    if fault == 'bad-code': other['checks'][0]['code'] = True
    def run(argv, **kw):
        import shlex
        assert argv[:2] == ['ssh', '-o']
        assert shlex.split(argv[-1]) == ['st', '--root', peer.root, 'ops', 'doctor', '--deploy', '--local', '--json']
        assert kw['timeout'] == 25
        return SimpleNamespace(returncode=1 if fault == 'exit-mismatch' else 0,
                               stdout='noise' if fault == 'bad-json' else json.dumps(other))
    monkeypatch.setattr(doc.subprocess, 'run', run)
    if fault == 'none':
        assert doc._read_peer(peer)['host'] == 'first'
    else:
        with pytest.raises(ValueError): doc._read_peer(peer)


@pytest.mark.parametrize('state', ['present', 'absent', 'no-control', 'unreachable', 'truncated'])
def test_graph_asserted_type_and_control(tmp_path, monkeypatch, state):
    from shantytown.answer import Answer
    monkeypatch.setattr(doc, 'deployment_default', lambda _, k: 'http://graph.example' if k == 'QUIPU_SERVER' else 'http://fleet.example/')
    class Graph:
        def __init__(self, **kwargs): pass
        def _query_answer(self, query):
            if state == 'unreachable': raise OSError('token secret')
            if 'LIMIT 1' in query:
                return Answer.complete_read([] if state == 'no-control' else [{'s': 'control'}], how='fixture')
            assert '<http://fleet.example/second> a ?t' in query
            assert 'FILTER(?t = <http://fleet.example/Host>)' in query
            if state == 'truncated':
                return Answer.capped([{'t': 'Host'}], how='fixture', caveat='truncated')
            return Answer.complete_read([{'t': 'Host'}] if state == 'present' else [], how='fixture')
    monkeypatch.setattr(doc, 'QuipuRegistry', Graph)
    row = doc._host_graph(tmp_path, 'second')
    assert row['code'] == (0 if state == 'present' else 1 if state == 'absent' else 2)
    assert 'secret' not in row['detail']


@pytest.mark.parametrize('extra', [['--install'], ['--relay'], ['--dry-run'], ['bobbin']])
def test_deploy_refuses_mutating_or_ambiguous_options(ready, extra):
    assert cli.main(['--root', str(ready), 'ops', 'doctor', '--deploy', *extra]) == 1


@pytest.mark.parametrize('rc', [False, True])
def test_codex_requires_standalone_only_when_rc_enabled(ready, monkeypatch, rc):
    path = ready / 'crew/worker.json'
    card = json.loads(path.read_text()); card['harness'] = 'codex'
    path.write_text(json.dumps(card))
    cli._emit_role_settings(ready, {'worker'}, harness_name='codex')
    monkeypatch.setenv('SHANTY_REMOTE_CONTROL', str(rc).lower())
    row = rows(doc.snapshot(ready))['worker/Codex RC']
    assert row['code'] == int(rc)


def test_workspace_fallback_and_duplicates(ready):
    role = ready / 'settings/worker.settings.json'
    data = json.loads(role.read_text())
    card = json.loads((ready / 'crew/worker.json').read_text())
    consent = Path(card['workspace']) / '.claude/settings.local.json'
    consent.parent.mkdir()
    consent.write_text(json.dumps(data))
    assert rows(doc.snapshot(ready))['worker/Stop capture']['code'] == 1
    role.write_text('{"hooks":{}}')
    assert rows(doc.snapshot(ready))['worker/Stop capture']['code'] == 0
    data['hooks']['Stop'] = [{'hooks': [{'command': 'python -m shantytown.stats capture --root /wrong'}]}]
    consent.write_text(json.dumps(data))
    assert rows(doc.snapshot(ready))['worker/Stop capture']['code'] == 1
