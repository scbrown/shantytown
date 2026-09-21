"""Plain-shell CLI configuration and refusal before graph I/O."""
import os

import pytest

from shantytown import cli
from shantytown.quipu import DEFAULT_ONTO, QuipuRegistry, request_headers, resolve_onto
from shantytown.quipu_events import QuipuEvents


@pytest.mark.parametrize('command', [['crew'], ['roles', 'sync'], ['ops', 'subscribe']])
def test_all_dispatches_carry_config_and_restore_environment(tmp_path, monkeypatch, command):
    token = tmp_path / 'token'
    token.write_text('fixture-secret')
    (tmp_path / 'shantytown.toml').write_text(
        '[env]\nQUIPU_SERVER="http://configured.test"\n'
        'SHANTY_ONTO_NS="http://configured.test/ontology#"\n'
        f'QUIPU_AUTH_TOKEN_FILE="{token}"\nEXTRA_SETTING="configured"\n')
    monkeypatch.setenv('QUIPU_SERVER', 'http://ambient.test')
    monkeypatch.delenv('SHANTY_ONTO_NS', raising=False)
    monkeypatch.delenv('QUIPU_AUTH_TOKEN', raising=False)
    monkeypatch.delenv('QUIPU_AUTH_TOKEN_FILE', raising=False)
    monkeypatch.delenv('EXTRA_SETTING', raising=False)

    def dispatch(a):
        # Unrooted readers and both graph adapters see the selected deployment.
        for client in (QuipuRegistry(), QuipuEvents()):
            assert client.server == 'http://configured.test'
            assert client.onto == 'http://configured.test/ontology#'
        assert request_headers()['Authorization'] == 'Bearer fixture-secret'
        assert os.environ['EXTRA_SETTING'] == 'configured'
        return 17

    monkeypatch.setattr(cli, '_run_command', dispatch)
    assert cli.main(['--root', str(tmp_path), *command]) == 17
    assert os.environ['QUIPU_SERVER'] == 'http://ambient.test'
    assert 'SHANTY_ONTO_NS' not in os.environ
    assert 'EXTRA_SETTING' not in os.environ


@pytest.mark.parametrize('namespace', [None, DEFAULT_ONTO])
@pytest.mark.parametrize('client', [QuipuRegistry, QuipuEvents])
def test_cli_refuses_missing_or_example_identity_before_network(
        tmp_path, monkeypatch, capsys, namespace, client):
    monkeypatch.delenv('SHANTY_ONTO_NS', raising=False)
    if namespace:
        (tmp_path / 'shantytown.toml').write_text(
            f'[env]\nSHANTY_ONTO_NS="{namespace}"\n')
    def forbidden(*args, **kwargs):
        pytest.fail('namespace refusal must precede any graph request')
    monkeypatch.setattr('urllib.request.urlopen', forbidden)
    monkeypatch.setattr(cli, '_run_command', lambda a: client())
    assert cli.main(['--root', str(tmp_path), 'crew']) == cli.REFUSED
    assert 'configure SHANTY_ONTO_NS' in capsys.readouterr().err
    # Refusal must not leave strict CLI policy active for subsequent library use.
    with pytest.warns(RuntimeWarning, match='documentation-only'):
        assert resolve_onto() == DEFAULT_ONTO


def test_local_command_needs_no_graph_namespace(tmp_path, monkeypatch):
    monkeypatch.delenv('SHANTY_ONTO_NS', raising=False)
    assert cli.main(['--root', str(tmp_path), 'help']) == cli.OK


def test_unexpected_failure_restores_environment(tmp_path, monkeypatch):
    (tmp_path / 'shantytown.toml').write_text('[env]\nEXTRA_SETTING="temporary"\n')
    monkeypatch.setenv('EXTRA_SETTING', 'original')
    def fail(a):
        raise RuntimeError('handler failed')
    monkeypatch.setattr(cli, '_run_command', fail)
    with pytest.raises(RuntimeError, match='handler failed'):
        cli.main(['--root', str(tmp_path), 'crew'])
    assert os.environ['EXTRA_SETTING'] == 'original'


def test_real_roles_command_refuses_before_query(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv('SHANTY_ONTO_NS', raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail('unconfigured CLI must not query even a populated graph')
    monkeypatch.setattr(QuipuRegistry, '_query', forbidden)
    rc = cli.main(['--root', str(tmp_path), '--registry', 'quipu', 'roles', '--check'])
    assert rc != cli.OK
    assert 'configure SHANTY_ONTO_NS' in capsys.readouterr().err
