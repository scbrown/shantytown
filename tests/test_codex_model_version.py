"""A model policy must not strand a role whose package updater has not run."""
import subprocess

import pytest

from shantytown import cli, harness
from shantytown.protocols import Agent
from shantytown.tmux import NullPanes


def _binary(path, version):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nprintf "%s\\n" "codex-cli ' + version + '"\n')
    path.chmod(0o755)
    return path


def _world(tmp_path, version, *, selection='default'):
    root = tmp_path / 'r'
    root.mkdir()
    config = '[env]\nSHANTY_REMOTE_CONTROL="true"\n'
    if selection == 'default':
        config += '[model]\ndefault="gpt-6.1-sol"\n'
    elif selection == 'role':
        config += '[model.by_role]\nlead="gpt-6.1-sol"\n'
    (root / 'shantytown.toml').write_text(config)
    home = root / 'settings/codex/lead'
    payload = _binary(home / 'packages/standalone/releases' /
                      (version + '-test') / 'codex', version)
    card = Agent(name='ada', role='lead', harness='codex',
                 model='gpt-6.1-sol' if selection == 'card' else None)
    return root, home, payload, card


@pytest.mark.parametrize('selection', ['default', 'role', 'card'])
def test_stale_lead_package_refused_for_resolved_model(tmp_path, selection):
    root, home, _, card = _world(tmp_path, '0.152.1', selection=selection)
    with pytest.raises(harness.Unsupported) as error:
        harness.get("codex").launch(card, str(home / 'config.toml'), root=root)
    text = str(error.value)
    assert '0.152.1' in text and 'gpt-6.1-sol' in text and '>= 0.160.0' in text
    assert str(home) in text and 'Update' in text


@pytest.mark.parametrize('version', ['0.160.0', '0.161.0', '0.200.0'])
def test_compatible_lead_package_keeps_selected_model(tmp_path, version):
    root, home, _, card = _world(tmp_path, version)
    launch = harness.get("codex").launch(card, str(home / 'config.toml'), root=root)
    assert '--model gpt-6.1-sol' in launch
    assert 'codex remote-control start' in launch


def test_payload_version_wins_over_release_directory_label(tmp_path):
    root, home, payload, card = _world(tmp_path, '0.161.0')
    _binary(payload, '0.152.1')
    with pytest.raises(harness.Unsupported, match='Codex 0.152.1'):
        harness.get("codex").launch(card, str(home / 'config.toml'), root=root)


@pytest.mark.parametrize('failure', ['malformed', 'nonzero', 'missing', 'timeout'])
def test_unknown_version_never_passes_known_floor(tmp_path, monkeypatch, failure):
    home = tmp_path / 'home'
    payload = _binary(tmp_path / 'codex', '0.161.0')
    if failure == 'malformed':
        _binary(payload, 'not-a-version')
    elif failure == 'nonzero':
        payload.write_text('#!/bin/sh\necho "codex-cli 0.161.0"\nexit 1\n')
    elif failure == 'missing':
        payload.unlink()
    else:
        def timeout(args, **kwargs):
            assert args == [str(payload), '--version']
            assert kwargs['timeout'] == 5
            assert kwargs['env']['CODEX_HOME'] == str(home)
            raise subprocess.TimeoutExpired(args, 5)
        monkeypatch.setattr(harness.subprocess, 'run', timeout)
    with pytest.raises(harness.Unsupported, match='Codex unknown'):
        harness.require_codex_model_version('gpt-6.1-sol', payload, home)


@pytest.mark.parametrize('version,accepted', [('0.152.1', False), ('0.161.0', True)])
def test_local_path_binary_is_checked(tmp_path, monkeypatch, version, accepted):
    payload = _binary(tmp_path / 'bin/codex', version)
    monkeypatch.setenv('PATH', str(payload.parent))
    card = Agent(name='ada', role='lead', model='gpt-6.1-sol')
    if accepted:
        assert '--model gpt-6.1-sol' in harness.get("codex").launch(card, str(tmp_path / 'config.toml'))
    else:
        with pytest.raises(harness.Unsupported, match='Codex 0.152.1'):
            harness.get("codex").launch(card, str(tmp_path / 'config.toml'))


def test_missing_local_binary_refused_for_known_floor(tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', str(tmp_path / 'empty'))
    with pytest.raises(harness.Unsupported, match='codex not found'):
        harness.get("codex").launch(Agent(name='ada', model='gpt-6.1-sol'), str(tmp_path / 'config.toml'))


@pytest.mark.parametrize('model', [None, 'custom-model', 'gpt-5.6-sol'])
def test_other_models_do_not_gain_a_version_prerequisite(tmp_path, monkeypatch, model):
    def unexpected(*args, **kwargs):
        pytest.fail('No known compatibility floor should mean no version probe')
    monkeypatch.setattr(harness.subprocess, 'run', unexpected)
    launch = harness.get("codex").launch(Agent(name='ada', model=model), str(tmp_path / 'config.toml'))
    assert ('--model' in launch) == (model is not None)


@pytest.mark.parametrize('version,expected', [('0.152.1', cli.REFUSED), ('0.161.0', cli.OK)])
def test_agent_new_checks_stale_and_fresh_lead_before_creating_session(tmp_path, monkeypatch, capsys, version, expected):
    import json
    from types import SimpleNamespace
    root, home, _, card = _world(tmp_path, version)
    (home / 'config.toml').write_text('')
    (root / 'crew').mkdir()
    (root / 'crew/ada.json').write_text(json.dumps({
        'role': 'lead', 'harness': 'codex', 'pane': 'crew-ada', 'dangerous': False,
    }))
    panes = NullPanes(live=set())
    monkeypatch.setattr(cli, 'Tmux', lambda *a, **kw: panes)
    args = SimpleNamespace(root=root, agent=card.name, dry_run=True,
                           backend='files', repo=None)
    assert cli._cmd_new(args) == expected
    output = capsys.readouterr()
    if expected == cli.REFUSED:
        assert '0.152.1' in output.err
    else:
        assert 'would launch' in output.out and '--model gpt-6.1-sol' in output.out
    assert not panes.sent and not panes.exists('crew-ada')
