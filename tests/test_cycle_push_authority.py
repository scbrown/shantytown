"""A push authority refusal must not strand an agent's clean committed work."""
from unittest.mock import Mock

import pytest

from shantytown import cycle, workspace
from tests.test_push_every_remote import two_remotes, _git, _commit


@pytest.mark.parametrize('dirty', [False, True])
def test_authority_refusal_is_notice_only_for_committed_work(two_remotes, dirty):
    _, tree, _, _ = two_remotes
    _commit(tree, 'preserved')
    _git(tree, 'config', '--unset', 'remote.forge.st-push-allowed')
    if dirty:
        (tree / 'f.txt').write_text('unfinished work\n')
    head = _git(tree, 'rev-parse', 'HEAD')
    content = (tree / 'f.txt').read_bytes()
    push = Mock(side_effect=AssertionError('must not contact a remote'))
    refused = workspace.push_every_remote(tree, 'HEAD', push_run=push)
    assert len(refused) == 1 and not refused[0].ok
    reachable = Mock(return_value=True)
    verdict = cycle.assess('worker', [tree], 'saved checkpoint',
                           workspace.tree_staleness, reachable=reachable)
    assert verdict.ok is not dirty
    if not dirty:
        assert len(verdict.stranded) == 1
        assert 'authority' in verdict.render().lower()
        assert 'UNREACHABLE' not in verdict.render()
        reachable.assert_not_called()
    else:
        assert verdict.risks[0].dirty
    push.assert_not_called()
    assert _git(tree, 'rev-parse', 'HEAD') == head
    assert (tree / 'f.txt').read_bytes() == content


def test_approved_peers_still_require_publication(two_remotes):
    _, tree, _, _ = two_remotes
    _commit(tree, 'preserved')
    verdict = cycle.assess('worker', [tree], 'saved checkpoint',
                           workspace.tree_staleness, reachable=lambda _: True)
    assert not verdict.ok
    assert verdict.risks[0].unpushed == 1


def test_refused_down_agent_resumes_without_loss_override(tmp_path, monkeypatch):
    from shantytown import cli
    from tests.test_cycle_fetch_timeout import world
    a, upstream, tree, _, requests, actions = world(tmp_path, monkeypatch)
    _commit(tree, 'preserved')
    _git(tree, 'remote', 'add', 'foreign', str(tmp_path / 'foreign.git'))
    requests.mark_refused('worker', 'previous loss gate refusal')
    head = _git(tree, 'rev-parse', 'HEAD')
    a._automatic_cycle = True
    assert cli._cmd_cycle(a) == cli.OK
    assert actions == ['stop', 'launch', 'dispatch']
    assert requests.pending() == {}
    assert _git(tree, 'rev-parse', 'HEAD') == head
    assert _git(upstream, 'rev-parse', 'HEAD') != head
