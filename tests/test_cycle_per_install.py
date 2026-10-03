"""Declared install changes survive a cycle without masking actual work."""
import pytest

from shantytown import cycle, workspace
from tests.test_push_every_remote import two_remotes, _git


@pytest.fixture
def install_tree(two_remotes):
    _, tree, _, _ = two_remotes
    (tree / '.st-per-install').write_text('# host wiring\nf.txt\n')
    (tree / 'work.txt').write_text('published work\n')
    _git(tree, 'add', '.st-per-install', 'work.txt')
    _git(tree, 'commit', '-qm', 'declare install path')
    assert all(o.ok for o in workspace.push_every_remote(tree, 'HEAD'))
    (tree / 'f.txt').write_text('local install wiring\n')
    return tree


def judge(tree):
    return cycle.assess('worker', [tree], 'checkpoint',
        lambda p: workspace.tree_staleness(p, cycle_per_install=True),
        reachable=lambda _: True)


def test_declared_modification_cycles_but_refresh_still_sees_dirt(install_tree):
    tree = install_tree
    before = (tree / 'f.txt').read_bytes()
    result = judge(tree)
    assert result.ok
    assert 'per-install, not loss' in result.render()
    assert 'f.txt' in result.render()
    assert workspace.tree_staleness(tree).dirty
    assert (tree / 'f.txt').read_bytes() == before
    assert _git(tree, 'ls-files', '-v', 'f.txt').startswith('H ')


@pytest.mark.parametrize('change', ['staged', 'deleted', 'renamed', 'other', 'manifest', 'symlink'])
def test_real_work_still_blocks(install_tree, change):
    tree = install_tree
    if change == 'staged':
        _git(tree, 'add', 'f.txt')
    elif change == 'deleted':
        (tree / 'f.txt').unlink()
    elif change == 'renamed':
        _git(tree, 'mv', 'f.txt', 'renamed.txt')
    elif change == 'other':
        (tree / 'work.txt').write_text('unfinished work\n')
    elif change == 'symlink':
        (tree / 'f.txt').unlink()
        (tree / 'f.txt').symlink_to('work.txt')
    else:
        (tree / '.st-per-install').write_text('f.txt\n.st-per-install\n')
    assert not judge(tree).ok


@pytest.mark.parametrize('declaration', ['../f.txt', '/f.txt', '*.txt', '.', '.st-per-install'])
def test_invalid_declaration_does_not_exempt(install_tree, declaration):
    tree = install_tree
    (tree / '.st-per-install').write_text(declaration + '\n')
    _git(tree, 'add', '.st-per-install')
    _git(tree, 'commit', '-qm', 'invalid declaration')
    assert all(o.ok for o in workspace.push_every_remote(tree, 'HEAD'))
    assert not judge(tree).ok


def test_cycle_command_honors_declaration_and_clears_refusal(tmp_path, monkeypatch):
    from shantytown import cli
    from tests.test_cycle_fetch_timeout import world
    a, _, tree, _, requests, actions = world(tmp_path, monkeypatch)
    (tree / '.st-per-install').write_text('a.txt\n')
    (tree / 'a.txt').write_text('portable\n')
    _git(tree, 'add', '.st-per-install', 'a.txt')
    _git(tree, 'commit', '-qm', 'declare install path')
    _git(tree, 'push', 'tracked', 'HEAD:refs/heads/install-policy')
    (tree / 'a.txt').write_text('install wiring\n')
    requests.mark_refused('worker', 'previous dirty gate refusal')
    a._automatic_cycle = True
    assert cli._cmd_cycle(a) == cli.OK
    assert actions == ['stop', 'launch', 'dispatch']
    assert requests.pending() == {}
    assert (tree / 'a.txt').read_text() == 'install wiring\n'
