import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from shantytown import sling
from shantytown.files import FilesTracker
from shantytown.inbox import FilesInbox
from shantytown.protocols import Agent


def boss(**kw):
    return replace(Agent('chief', 'administrator', roles=('administrator', 'executive')), **kw)


def setup(tmp_path):
    root = tmp_path / 'items'
    root.mkdir()
    (root / 'design-1.json').write_text(json.dumps(dict(title='Design', description='A real design')))
    (root / 'child-1.json').write_text(json.dumps(dict(title='Child', parent='design-1')))
    return FilesTracker(root), FilesInbox(tmp_path / 'inbox')


def test_role_resolution_not_name():
    assert sling.executive([boss(name='different')]).name == 'different'
    for cards in ([], [boss(), boss(name='other')], [boss(role='worker')], [boss(retired=True)]):
        with pytest.raises(sling.Refused):
            sling.executive(cards)


def test_design_body_and_children(tmp_path):
    tracker, _ = setup(tmp_path)
    assert sling.design(tracker, 'design-1')['children'] == ['child-1']
    path = tracker._path('design-1')
    path.write_text(json.dumps(dict(title='Bare', description='  ')))
    with pytest.raises(sling.Refused):
        sling.design(tracker, 'design-1')
    path.write_text(json.dumps(dict(title='Design', design='Separate design body')))
    assert sling.design(tracker, 'design-1')['design']


def test_long_note_full_comment_bounded_pointer_idempotent_closed_receipt(tmp_path):
    tracker, box = setup(tmp_path)
    note = '設計' * 4000
    plan = sling.prepare(sling.design(tracker, 'design-1'), 'author', boss(), note)
    assert len(plan['payload'].encode()) <= 493
    receipt = sling.deliver(plan, tracker, box, tmp_path)
    box.mark_read('chief', ids=[receipt.id])
    assert not box.unread('chief')
    assert sling.deliver(plan, tracker, box, tmp_path).id == receipt.id
    row = json.loads(tracker._path('design-1').read_text())
    assert len(row['comments']) == 1
    recorded = json.loads(row['comments'][0]['text'].split('\n', 1)[1])
    assert recorded['note'] == note
    assert recorded['children'] == ['child-1']
    event = json.loads(next((tmp_path / 'sling').glob('*.json')).read_text())
    assert event['state'] == 'delivered' and event['receipt'] == receipt.id
    assert event['sender'] == 'author' and event['at']


def test_lost_create_response_recovers_by_readback(tmp_path):
    tracker, box = setup(tmp_path)
    plan = sling.prepare(sling.design(tracker, 'design-1'), 'author', boss(), '')
    real = box.deliver
    def lost(*a, **kw):
        real(*a, **kw)
        raise RuntimeError('response lost')
    box.deliver = lost
    with pytest.raises(RuntimeError, match='response lost'):
        sling.deliver(plan, tracker, box, tmp_path)
    box.deliver = lambda *a, **kw: pytest.fail('duplicate create')
    assert sling.deliver(plan, tracker, box, tmp_path).id


def test_missing_receipt_never_success(tmp_path):
    tracker, box = setup(tmp_path)
    box.find_delivery = lambda *args: None
    plan = sling.prepare(sling.design(tracker, 'design-1'), 'author', boss(), '')
    with pytest.raises(RuntimeError, match='read-back missing'):
        sling.deliver(plan, tracker, box, tmp_path)


def test_utf8_sender_budget():
    with pytest.raises(sling.Refused, match='byte budget'):
        sling.prepare(dict(item='d', children=[]), '猫' * 200, boss(), '')


def test_cli_local_dry_run_then_durable_unread(tmp_path, monkeypatch, capsys):
    from shantytown import cli
    from shantytown.files import FilesRegistry
    from shantytown.tmux import NullPanes
    tracker, _ = setup(tmp_path)
    FilesRegistry(tmp_path / 'crew').set(boss(pane='%9'))
    panes = NullPanes()
    monkeypatch.setattr(cli, '_panes', lambda a: panes)
    monkeypatch.setenv('SHANTY_AGENT', 'author')
    args = ['--root', str(tmp_path), '--backend', 'files', 'sling', 'design-1']
    assert cli.main(args + ['--dry-run']) == 0
    assert not (tmp_path / 'sling').exists()
    assert not (tmp_path / 'inbox').exists()
    assert not panes.sent
    assert 'chief' in capsys.readouterr().out
    assert cli.main(args) == 0
    assert len(FilesInbox(tmp_path / 'inbox').unread('chief')) == 1
    assert len(panes.sent) == 1
    assert not json.loads(tracker._path('design-1').read_text()).get('assignee')


def test_cli_identity_refusal_before_write(tmp_path, monkeypatch):
    from shantytown import cli
    monkeypatch.setattr(cli, '_verified_sender', lambda *a: (None, False))
    assert cli.main(['--root', str(tmp_path), 'sling', 'x']) == 1
    assert not (tmp_path / 'sling').exists()


def test_br_design_uses_parent_child_edges_only(tmp_path, monkeypatch):
    import subprocess
    from shantytown.br import BrTracker
    tracker = BrTracker(repo=str(tmp_path))
    monkeypatch.setattr(tracker, '_bd_for', lambda *a: subprocess.CompletedProcess([], 0,
        json.dumps([dict(id='x', title='Design', design='body', dependents=[
            dict(id='child', dependency_type='parent-child'),
            dict(id='blocker', dependency_type='blocks')])]), ''))
    assert sling.design(tracker, 'x')['children'] == ['child']


def test_partial_registry_cannot_choose_executive(tmp_path, monkeypatch):
    from shantytown import cli, sling_cli
    from shantytown.answer import Answer, PartialAnswer
    monkeypatch.setattr(cli, '_registry', lambda a: SimpleNamespace(
        all=lambda: Answer([boss()], 'truncated fixture', False, 'row cap')))
    with pytest.raises(PartialAnswer):
        sling_cli.target(SimpleNamespace(root=tmp_path, registry='files'))


def test_executive_cannot_replace_tree_position(tmp_path):
    from shantytown.tier import plan_role_set
    from shantytown.files import FilesRegistry
    registry = FilesRegistry(tmp_path)
    registry.set(boss())
    with pytest.raises(ValueError, match='additive marker'):
        plan_role_set(registry, 'chief', 'executive')


def test_placed_executive_requires_local_host_identity(tmp_path, monkeypatch):
    from shantytown import cli
    from shantytown.files import FilesRegistry
    from shantytown.tmux import NullPanes
    setup(tmp_path)
    FilesRegistry(tmp_path / 'crew').set(boss(host='elsewhere', pane='%1'))
    panes = NullPanes()
    monkeypatch.setattr(cli, '_panes', lambda a: panes)
    monkeypatch.setenv('SHANTY_AGENT', 'author')
    assert cli.main(['--root', str(tmp_path), '--backend', 'files',
                     'sling', 'design-1']) == 1
    assert not panes.sent
    assert not (tmp_path / 'sling').exists()
    assert not (tmp_path / 'inbox').exists()
