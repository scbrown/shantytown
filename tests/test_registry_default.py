"""Deployment identity selection uses the same precedence as other defaults."""
import pytest
from shantytown import cli


def setup_registry(monkeypatch, tmp_path, declared=None, ambient=None):
    monkeypatch.delenv('SHANTY_REGISTRY', raising=False)
    monkeypatch.delenv('SHANTY_CONFIG_ENTITY', raising=False)
    tmp_path.joinpath('crew').mkdir()
    text = '[env]\n' + (f'SHANTY_REGISTRY="{declared}"\n' if declared else '')
    tmp_path.joinpath('shantytown.toml').write_text(text)
    if ambient is not None:
        monkeypatch.setenv('SHANTY_REGISTRY', ambient)
    monkeypatch.setattr(cli, 'FilesRegistry', lambda *a, **kw: 'files')
    monkeypatch.setattr(cli, 'QuipuRegistry', lambda *a, **kw: 'quipu')
    monkeypatch.setattr(cli.config, 'TomlRegistry', lambda *a, **kw: 'toml')


@pytest.mark.parametrize('declared,ambient,explicit,expected', [
    (None, None, None, 'files'),
    (None, 'quipu', None, 'quipu'),
    ('toml', 'quipu', None, 'toml'),
    ('quipu', 'files', 'files', 'files'),
    ('toml', None, 'quipu', 'quipu'),
    ('typo', None, 'files', 'files'),
])
def test_registry_precedence(monkeypatch, tmp_path, declared, ambient, explicit, expected):
    setup_registry(monkeypatch, tmp_path, declared, ambient)
    options = ['--registry', explicit] if explicit else []
    args = cli._parse_args(['--root', str(tmp_path), *options, 'crew'])
    assert cli._registry(args) == expected


@pytest.mark.parametrize('declared,ambient', [('typo', None), (None, 'typo')])
def test_invalid_default_refuses_instead_of_reading_files(monkeypatch, tmp_path, declared, ambient):
    setup_registry(monkeypatch, tmp_path, declared, ambient)
    args = cli._parse_args(['--root', str(tmp_path), 'crew'])
    with pytest.raises(SystemExit, match='SHANTY_REGISTRY'):
        cli._registry(args)


def test_main_resolves_registry_for_handlers_that_inspect_arguments(monkeypatch, tmp_path):
    setup_registry(monkeypatch, tmp_path, 'toml')
    selected = []
    monkeypatch.setattr(cli, '_run_command', lambda args: selected.append(args.registry) or 0)
    assert cli.main(['--root', str(tmp_path), 'crew']) == 0
    assert selected == ['toml']
