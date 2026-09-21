"""Second-host setup refuses unknown scope and unsupported sync semantics."""
import json
from pathlib import Path

import pytest

from shantytown import cli, config, scaffold, selfcheck, sync_version
from shantytown.answer import Answer
from shantytown.protocols import Agent


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    for key in ('QUIPU_SERVER', 'SHANTY_ONTO_NS', 'SHANTY_HOST', 'SHANTY_CANONICAL_SOURCE'):
        monkeypatch.delenv(key, raising=False)


def fleet(monkeypatch, members=None, error=None):
    class Registry:
        def __init__(self, **kw):
            assert kw['server'] == 'http://graph.example'
            assert kw['onto'] == 'http://fleet.example/'
        def all(self):
            if error:
                raise error
            return Answer.complete_read(members or [], how='fixture')
    monkeypatch.setattr(cli, 'QuipuRegistry', Registry)


def init_args(root):
    return ['--root', str(root), 'fleet', 'init', '-y', '--admin', 'secondary',
            '--quipu-server', 'http://graph.example',
            '--ontology-namespace', 'http://fleet.example/']


def test_existing_fleet_needs_declared_host_before_any_write(tmp_path, monkeypatch, capsys):
    fleet(monkeypatch, [Agent(name='primary', role='administrator', host='host-a')])
    root = tmp_path / 'new'
    assert cli.main(init_args(root)) == cli.REFUSED
    assert not root.exists()
    assert '--host NAME' in capsys.readouterr().err


def test_graph_unavailable_is_not_an_empty_fleet(tmp_path, monkeypatch):
    fleet(monkeypatch, error=OSError('offline'))
    root = tmp_path / 'new'
    assert cli.main(init_args(root) + ['--host', 'host-b']) == cli.CANNOT_TELL
    assert not root.exists()


def test_second_host_scaffolds_identity_peer_and_source(tmp_path, monkeypatch):
    fleet(monkeypatch, [Agent(name='primary', role='administrator', host='host-a')])
    root = tmp_path / 'new'
    assert cli.main(init_args(root) + ['--host', 'host-b', '--peer',
        'host-a=operator@primary.example,/opt/fleet/.shanty',
        '--canonical-source', '/opt/src/shantytown']) == cli.OK
    cfg = config.load(root)
    assert cfg.host_name == 'host-b'
    assert cfg.host_min_sync_version == 1
    assert cfg.host_peers['host-a'].root == '/opt/fleet/.shanty'
    assert cfg.env['QUIPU_SERVER'] == 'http://graph.example'
    assert cfg.env['SHANTY_ONTO_NS'] == 'http://fleet.example/'
    assert cfg.env['SHANTY_CANONICAL_SOURCE'] == '/opt/src/shantytown'
    card = json.loads((root / 'crew/secondary.json').read_text())
    assert card['host'] == 'host-b'
    assert card['role'] == 'administrator'
    assert not (root / 'crew/primary.json').exists()


@pytest.mark.parametrize('extra', [[], ['--force'], ['--allow-breakage'], ['--dry-run']])
def test_unsupported_floor_refuses_before_source_query(tmp_path, monkeypatch, extra):
    (tmp_path / 'shantytown.toml').write_text('[host]\nname="host-b"\nmin_sync_version=2\n')
    monkeypatch.setattr(cli, '_rooted_quipu', lambda _: pytest.fail('source queried'))
    assert cli.main(['--root', str(tmp_path), 'roles', 'sync', *extra]) == cli.REFUSED
    assert not (tmp_path / 'crew').exists()


def test_old_protocol_build_refuses_declared_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_version, 'HOST_SYNC_VERSION', 0)
    assert cli.main(['--root', str(tmp_path), 'roles', 'sync',
                     '--require-host-sync', '1']) == cli.REFUSED
    assert not (tmp_path / 'crew').exists()


@pytest.mark.parametrize('value', ['true', '0', '-1', '"1"'])
def test_malformed_floor_is_not_silently_defaulted(tmp_path, value):
    (tmp_path / 'shantytown.toml').write_text('[host]\nmin_sync_version='+value+'\n')
    assert cli.main(['--root', str(tmp_path), 'roles', 'sync']) == cli.REFUSED
    assert not (tmp_path / 'crew').exists()


def test_flag_cannot_weaken_configured_floor(tmp_path):
    (tmp_path / 'shantytown.toml').write_text('[host]\nmin_sync_version=2\n')
    assert cli.main(['--root', str(tmp_path), 'roles', 'sync',
                     '--require-host-sync', '1']) == cli.REFUSED


def test_peer_requires_host_and_valid_shape(tmp_path):
    args = ['--root', str(tmp_path), 'fleet', 'init', '-y']
    assert cli.main(args + ['--peer', 'other=u@host,/store']) == cli.REFUSED
    assert cli.main(args + ['--host', 'here', '--peer', 'malformed']) == cli.REFUSED
    assert not (tmp_path / 'crew').exists()


def test_config_quotes_peer_paths_and_env():
    import tomllib
    a = scaffold.make_answers(admin='admin', host='secondary',
        peers=[('primary', 'user@host', '/a/"b')],
        env=[('SHANTY_CANONICAL_SOURCE', '/a/"source')])
    parsed = tomllib.loads(scaffold.config_text(a))
    assert parsed['host']['peers']['primary']['root'] == '/a/"b'


def test_doctor_gives_a_concrete_local_source_recipe(monkeypatch):
    monkeypatch.setattr(selfcheck, 'canonical_source', lambda _: None)
    report = selfcheck.check_self()
    assert report.verdict == selfcheck.CANNOT_TELL
    assert 'git clone https://github.com/scbrown/shantytown.git' in report.note
    assert 'pipx install --force --editable' in report.note
    assert 'local source path' in report.note


def test_supported_floor_projects_only_local_members(tmp_path, monkeypatch):
    (tmp_path / 'shantytown.toml').write_text('[host]\nname="host-b"\nmin_sync_version=1\n')
    class Graph:
        def all(self):
            return Answer.complete_read([
                Agent(name='local-admin', role='administrator', host='host-b'),
                Agent(name='remote-admin', role='administrator', host='host-a'),
            ], how='fixture')
    monkeypatch.setattr(cli, '_rooted_quipu', lambda _: Graph())
    assert cli.main(['--root', str(tmp_path), 'roles', 'sync',
                     '--require-host-sync', '1']) == cli.OK
    assert (tmp_path / 'crew/local-admin.json').exists()
    assert not (tmp_path / 'crew/remote-admin.json').exists()


def test_forced_init_cannot_claim_to_change_kept_host(tmp_path):
    cfg = tmp_path / 'shantytown.toml'
    cfg.write_text('[host]\nname="old"\n')
    before = cfg.read_bytes()
    assert cli.main(['--root', str(tmp_path), 'fleet', 'init', '-y',
                     '--force', '--host', 'new']) == cli.REFUSED
    assert cfg.read_bytes() == before
    assert not (tmp_path / 'crew').exists()


def test_doctor_reads_canonical_source_from_deployment(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from shantytown import doctor
    (tmp_path / 'shantytown.toml').write_text(
        '[env]\nSHANTY_CANONICAL_SOURCE="/local/approved/source"\n')
    monkeypatch.setattr(doctor, 'detect_all', lambda *a, **kw: [])
    seen = {}
    class Observed(Exception):
        pass
    def check(**kwargs):
        seen.update(kwargs)
        raise Observed
    monkeypatch.setattr(selfcheck, 'check_self', check)
    with pytest.raises(Observed):
        cli._cmd_doctor(SimpleNamespace(root=tmp_path, tool=None, no_latest=True))
    assert seen == {'canonical': '/local/approved/source', 'remote': False}
