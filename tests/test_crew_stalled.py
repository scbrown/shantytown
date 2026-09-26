"""An idle pane with unfinished plate work is not spare crew capacity."""
import json

import pytest

from shantytown import br, cli, agent_hold
from shantytown.protocols import WorkItem
from tests.test_crew_work import IDLE_SCREEN, BUSY_SCREEN, SHELL_SCREEN, _Args, _Panes, _roster


def setup_crew(tmp_path, monkeypatch, screens, rows, failures=()):
    root = _roster(tmp_path, {name: 'p-' + name for name in screens})
    panes = _Panes({'p-' + name: screen for name, screen in screens.items()
                    if screen is not None})
    monkeypatch.setattr(cli, 'Tmux', lambda *_a, **_k: panes)
    calls = []
    def load(_):
        calls.append('list')
        return rows, failures
    monkeypatch.setattr(br, 'rows_partial', load)
    monkeypatch.setattr(br, 'ready_ids_or_none', lambda _: {row['id'] for row in rows})
    monkeypatch.setattr(cli, '_plate', lambda *_, **kw: br.plate_reader(
        object(), require_complete=kw.get('require_complete', False)))
    return root, calls


def row(name, status='in_progress'):
    return dict(id='task-' + name, title='unfinished ' + name,
                assignee=name, status=status)


def test_table_exposes_stall_without_reclassifying_other_panes(tmp_path, monkeypatch, capsys):
    root, calls = setup_crew(tmp_path, monkeypatch,
        dict(stuck=IDLE_SCREEN, working=BUSY_SCREEN, free=IDLE_SCREEN,
             empty=IDLE_SCREEN, unknown=SHELL_SCREEN, stopped=None),
        [row('stuck'), row('working'), row('free', 'open'), row('unknown'), row('stopped')])
    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out
    assert '1 stalled: stuck' in out
    assert '2 free: empty, free' in out
    assert '1 busy: working' in out
    assert '1 UNKNOWN work state: unknown' in out
    assert 'assigned: task-stuck' in out
    assert 'every live agent is mid-flight' not in out
    assert calls == ['list']  # verdict and assigned line share the store read


@pytest.mark.parametrize('mode', ['json', 'count'])
def test_machine_views_share_stall_classification(tmp_path, monkeypatch, capsys, mode):
    root, calls = setup_crew(tmp_path, monkeypatch,
        dict(stuck=IDLE_SCREEN, working=BUSY_SCREEN, free=IDLE_SCREEN),
        [row('stuck'), row('working')])
    args = _Args(root)
    setattr(args, mode, True)
    assert cli._cmd_crew(args) == cli.OK
    out = capsys.readouterr().out
    if mode == 'count':
        assert out.strip() == '1/2'
    else:
        states = {a['name']: a['work'] for a in json.loads(out)['agents']}
        assert states == dict(stuck='stalled', working='busy', free='idle')
    assert calls == ['list']


@pytest.mark.parametrize('rows', [[], [row('stuck')]])
def test_partial_store_never_claims_free_or_stalled(tmp_path, monkeypatch, capsys, rows):
    root, calls = setup_crew(tmp_path, monkeypatch, dict(stuck=IDLE_SCREEN),
                             rows, ['extra store unavailable'])
    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out
    assert '? (assignment unavailable)' in out
    assert '1 UNKNOWN work state: stuck' in out
    assert 'free:' not in out and 'stalled:' not in out
    assert calls == ['list']


def test_hold_wins_over_unfinished_plate(tmp_path, monkeypatch, capsys):
    root, _ = setup_crew(tmp_path, monkeypatch, dict(stuck=IDLE_SCREEN), [row('stuck')])
    agent_hold.hold(root, 'stuck', 'lead', 'capacity')
    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out
    assert 'held (by lead' in out
    assert 'stalled:' not in out


def test_plain_plate_reader_still_degrades_loudly_but_strict_reader_refuses(monkeypatch, capsys):
    monkeypatch.setattr(br, 'rows_partial', lambda _: ([row('stuck')], ['extra unavailable']))
    monkeypatch.setattr(br, 'ready_ids_or_none', lambda _: None)
    assert br.plate_reader(object())('stuck').status == 'in_progress'
    assert 'extra unavailable' in capsys.readouterr().err
    with pytest.raises(RuntimeError, match='extra unavailable'):
        br.plate_reader(object(), require_complete=True)('stuck')


@pytest.mark.parametrize('disposition, expected', [
    ('cycling', '—'), ('cycle-blocked', 'idle'), ('shell', 'idle+1sh'),
])
def test_lifecycle_and_background_work_win(tmp_path, monkeypatch, disposition, expected):
    root, _ = setup_crew(tmp_path, monkeypatch, dict(stuck=IDLE_SCREEN), [row('stuck')])
    args = _Args(root)
    panes = cli.Tmux()
    kwargs = {}
    if disposition == 'shell':
        monkeypatch.setattr(cli.triage_mod, 'running_shells', lambda _: 1)
    else:
        kwargs[disposition.replace('-', '_')] = {'stuck'}
    states = list(cli._crew_states(cli._registry(args).all().exact(), panes,
        cli._runtime(args, panes), untracked_root=root,
        plate=br.plate_reader(object(), require_complete=True), **kwargs))
    assert states[0][2] == expected
