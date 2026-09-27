"""A real receiving CLI, over the existing peer relay with isolated stores."""
import contextlib
import io
import json
import shlex
import subprocess

import pytest

from shantytown import cli
from shantytown.files import FilesRegistry
from shantytown.inbox import FilesInbox
from shantytown.protocols import Agent
from shantytown.tmux import NullPanes


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    roots = [tmp_path / name for name in ('desktop', 'laptop')]
    board = tmp_path / 'board'
    board.mkdir()
    (board / 'design-1.json').write_text(json.dumps(dict(title='Plan', description='The design')))
    panes = {name: NullPanes(screen='') for name in ('desktop', 'laptop')}
    for root, other in zip(roots, reversed(roots)):
        (root / 'crew').mkdir(parents=True)
        (root / 'items').symlink_to(board, target_is_directory=True)
        (root / 'shantytown.toml').write_text(
            f'[host]\nname="{root.name}"\n[host.peers.{other.name}]\n'
            f'ssh="user@{other.name}.example"\nroot="{other}"\n')
    FilesRegistry(roots[1] / 'crew').set(Agent('chief', 'administrator',
        pane='%1', host='laptop', roles=('administrator', 'executive')))
    FilesRegistry(roots[0] / 'crew').set(Agent('designer', 'worker', host='desktop'))
    monkeypatch.setattr(cli, '_panes', lambda a: panes[a.root.name])
    monkeypatch.setenv('SHANTY_AGENT', 'designer')
    calls = []
    real_run = subprocess.run

    def relay(argv, **kwargs):
        if argv[0] != 'ssh':
            return real_run(argv, **kwargs)
        cmd = shlex.split(argv[-1])
        args = cmd[cmd.index('st') + 1:]
        calls.append(args)
        sender = next((x.partition('=')[2] for x in cmd if x.startswith('SHANTY_AGENT=')), '')
        out, err = io.StringIO(), io.StringIO()
        with monkeypatch.context() as remote:
            remote.setenv('SHANTY_AGENT', sender)
            remote.setattr('sys.stdin', io.StringIO(kwargs.get('input', '')))
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = cli.main(args)
        return subprocess.CompletedProcess(argv, rc, out.getvalue(), err.getvalue())

    monkeypatch.setattr(subprocess, 'run', relay)
    return roots, board, panes, calls


def invoke(root, *args):
    return cli.main(['--root', str(root), '--backend', 'files', 'sling', 'design-1', *args])


def test_cross_host_durable_pointer_with_literal_note(fleet):
    roots, board, panes, calls = fleet
    note = "A note\n$(touch /never) `uname` 'quoted' " + '語' * 500
    assert invoke(roots[0], '--note', note) == 0
    assert not panes['desktop'].sent
    assert len(panes['laptop'].sent) == 1
    receipt = FilesInbox(roots[1] / 'inbox').unread('chief')
    assert len(receipt) == 1 and '[from designer]' in receipt[0].body
    assert not (roots[0] / 'inbox').exists()
    row = json.loads((board / 'design-1.json').read_text())
    assert note == json.loads(row['comments'][0]['text'].split('\n', 1)[1])['note']
    assert not row.get('assignee')
    assert sum('sling' in call for call in calls) == 1


def test_remote_preview_has_no_writes(fleet):
    roots, board, panes, _ = fleet
    before = (board / 'design-1.json').read_bytes()
    assert invoke(roots[0], '--dry-run') == 0
    assert before == (board / 'design-1.json').read_bytes()
    assert all(not (r / 'sling').exists() and not (r / 'inbox').exists() for r in roots)
    assert not any(p.sent for p in panes.values())


def test_receiving_host_and_executive_are_pinned(fleet):
    roots, _, panes, _ = fleet
    assert invoke(roots[1], '--receiving-host', 'desktop') == 1
    assert invoke(roots[1], '--executive', 'someone-else') == 1
    assert not any(p.sent for p in panes.values())


def test_retired_peer_is_not_an_executive(fleet):
    roots, _, panes, _ = fleet
    from dataclasses import replace
    registry = FilesRegistry(roots[1] / 'crew')
    registry.set(replace(registry.get('chief'), retired=True))
    assert invoke(roots[0]) == 1
    assert not any(p.sent for p in panes.values())
