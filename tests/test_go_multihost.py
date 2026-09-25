"""Dispatch runs the real receiving CLI, preserving its gates and shared-board writes."""
import contextlib
import io
import json
import shlex
import subprocess

import pytest

from shantytown import cli
from shantytown.tmux import NullPanes


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    roots = [tmp_path / name for name in ('desktop', 'laptop')]
    board = tmp_path / 'board'; board.mkdir()
    (board / 'work-1.json').write_text(json.dumps({'title': 'A shared task', 'status': 'open'}))
    panes = {name: NullPanes(screen='') for name in ('desktop', 'laptop')}
    for root, other in zip(roots, reversed(roots)):
        (root / 'crew').mkdir(parents=True)
        (root / 'items').symlink_to(board, target_is_directory=True)
        (root / 'shantytown.toml').write_text(
            f'[host]\nname="{root.name}"\n[host.peers.{other.name}]\n'
            f'ssh="user@{other.name}.example"\nroot="{other}"\n')
        for where in roots:
            (root / 'crew' / f'{where.name}-worker.json').write_text(json.dumps({
                'role': 'worker', 'host': where.name, 'pane': '%1'}))
    monkeypatch.setattr(cli, '_panes', lambda a: panes[a.root.name])
    monkeypatch.setattr(cli, '_keep_current', lambda *args: None)
    monkeypatch.setattr(cli, '_dispatch_gate', lambda a: None)
    calls = []
    real_run = subprocess.run

    def relay(argv, **kwargs):
        if argv[0] != 'ssh':
            return real_run(argv, **kwargs)
        calls.append((argv, kwargs))
        cmd = shlex.split(argv[-1]); args = cmd[cmd.index('st') + 1:]
        sender = next(x.partition('=')[2] for x in cmd if x.startswith('SHANTY_AGENT='))
        out, err = io.StringIO(), io.StringIO()
        with monkeypatch.context() as remote:
            remote.setenv('SHANTY_AGENT', sender)
            remote.setattr('sys.stdin', io.StringIO(kwargs['input']))
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = cli.main(args)
        return subprocess.CompletedProcess(argv, rc, out.getvalue(), err.getvalue())

    monkeypatch.setattr(subprocess, 'run', relay)
    return roots, board, panes, calls


def go(root, agent, *args):
    return cli.main(['--root', str(root), 'go', 'work-1', agent,
                     '--no-graph-context', 'isolated fixture', *args])


@pytest.mark.parametrize('source', [0, 1])
def test_both_directions_send_once_and_record_shared_assignment(fleet, monkeypatch, source):
    roots, board, panes, calls = fleet
    sender, receiver = roots[source], roots[1-source]
    monkeypatch.setenv('SHANTY_AGENT', 'coordinator')
    note = "line one\n$(touch /never) `uname` 'quoted'"
    assert go(sender, receiver.name+'-worker', '--note', note) == cli.OK
    assert len(calls) == 1
    assert calls[0][1]['input'] == note
    assert not panes[sender.name].sent
    assert len(panes[receiver.name].sent) == 1
    payload = panes[receiver.name].sent[0][1]
    assert 'coordinator' in payload and "$(touch /never) `uname` 'quoted'" in payload
    item = json.loads((board / 'work-1.json').read_text())
    assert item['status'] == 'in_progress' and item['assignee'] == receiver.name+'-worker'


def test_busy_destination_refuses_without_touching_colliding_local_pane(fleet):
    roots, board, panes, calls = fleet
    panes['laptop'].screen = 'esc to interrupt'
    assert go(roots[0], 'laptop-worker') == cli.REFUSED
    assert len(calls) == 1 and not any(p.sent for p in panes.values())
    assert json.loads((board / 'work-1.json').read_text())['status'] == 'open'


def test_dry_run_is_remote_triage_without_writes(fleet):
    roots, board, panes, calls = fleet
    assert go(roots[0], 'laptop-worker', '-n') == cli.OK
    assert '--dry-run' in calls[0][0][-1]
    assert not any(p.sent for p in panes.values())
    assert json.loads((board / 'work-1.json').read_text())['status'] == 'open'


def test_receiving_host_mismatch_cannot_relay_again(fleet):
    roots, board, panes, calls = fleet
    assert go(roots[1], 'desktop-worker', '--receiving-host', 'laptop') == cli.REFUSED
    assert not calls and not any(p.sent for p in panes.values())
    assert go(roots[1], 'laptop-worker', '--receiving-host', 'wrong-host') == cli.REFUSED


def test_offhost_without_peer_refuses(fleet):
    roots, board, panes, calls = fleet
    (roots[0] / 'shantytown.toml').write_text('[host]\nname="desktop"\n')
    assert go(roots[0], 'laptop-worker') == cli.REFUSED
    assert not calls and not any(p.sent for p in panes.values())


def test_closed_item_is_refused_on_destination(fleet):
    roots, board, panes, calls = fleet
    (board / 'work-1.json').write_text(json.dumps({'title': 'done', 'status': 'closed'}))
    assert go(roots[0], 'laptop-worker') == cli.REFUSED
    assert not any(p.sent for p in panes.values())


def test_ambiguous_ssh_failure_does_not_retry(fleet, monkeypatch, capsys):
    roots, _, panes, _ = fleet
    seen = []
    previous = subprocess.run
    def timeout(argv, **kwargs):
        if argv[0] != "ssh":
            return previous(argv, **kwargs)
        seen.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
    monkeypatch.setattr(subprocess, 'run', timeout)
    assert go(roots[0], 'laptop-worker') == cli.CANNOT_TELL
    assert len(seen) == 1 and not any(p.sent for p in panes.values())
    assert 'may have happened' in capsys.readouterr().err


def test_declared_board_transport_failure_never_counts_other_bd_stores(tmp_path, monkeypatch):
    from shantytown.br import BrTracker
    from shantytown import stores
    monkeypatch.setenv('SHANTY_BR_BIN', '/opt/bin/shared-board')
    monkeypatch.setattr(BrTracker, '_bd', lambda *args: subprocess.CompletedProcess(
        [], 1, json.dumps({'error': {'code': 'NOT_FOUND', 'message': 'unknown id'}}), ''))
    monkeypatch.setattr(stores, 'not_found_here', lambda *args: pytest.fail('local bd enumeration'))
    with pytest.raises(LookupError, match='NOT_FOUND.*shared-board'):
        BrTracker(repo=str(tmp_path)).get('work-missing')


def test_destination_governor_is_not_bypassed(fleet, monkeypatch):
    roots, board, panes, calls = fleet
    monkeypatch.setattr(cli, '_dispatch_gate', lambda a: (
        lambda item, agent=None: 'receiver budget held'))
    assert go(roots[0], 'laptop-worker') == cli.REFUSED
    assert len(calls) == 1 and not any(p.sent for p in panes.values())
    assert json.loads((board / 'work-1.json').read_text())['status'] == 'open'


def test_bad_host_config_cannot_fall_back_to_colliding_local_pane(fleet):
    roots, board, panes, calls = fleet
    (roots[0] / 'shantytown.toml').write_text('[host\nname="desktop"\n')
    assert go(roots[0], 'laptop-worker') == cli.CANNOT_TELL
    assert not calls and not any(p.sent for p in panes.values())
