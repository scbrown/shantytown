"""Priority advice must name real workable waiters without changing assignments."""
import json
from types import SimpleNamespace

import pytest

from shantytown import cli, priority_advisory as pa
from shantytown.tmux import NullPanes

HIGH = {'id': 'high', 'title': 'high work', 'priority': 1, 'status': 'open'}
LOW = {'id': 'low', 'title': 'low work', 'priority': 2, 'status': 'open'}


@pytest.mark.parametrize('change', [
    {'status': 'deferred'}, {'status': 'closed'}, {'labels': ['decision-needed']},
    {'labels': ['blocked:external']}, {'labels': ['blocked:bead']},
    {'labels': ['parked:by-design']}, {'labels': ['anchor']},
    {'defer_until': '2999-01-01T00:00:00Z'}, {'blocked_by': ['other']},
    {'assignee': 'live'}, {'assignee': 'unknown'}, {'priority': 2},
    {'priority': None}, {'priority': True}, {'title': 'inbox: a message'},
])
def test_handled_or_unavailable_work_never_warns(change):
    assert pa.advice([HIGH | change], LOW, {'down'}) == ''


def test_down_owner_counts_and_limit_does_not_hide_total():
    rows = [HIGH | {'id': f'waiting-{i}', 'assignee': 'crew/down'} for i in range(5)]
    note = pa.advice(rows, LOW, {'down'})
    assert '5 higher-priority' in note
    assert 'waiting-0' in note and 'waiting-2' in note
    assert 'waiting-3' not in note
    assert rows[0]['assignee'] == 'crew/down'


def _store(tmp_path):
    (tmp_path / 'crew').mkdir()
    (tmp_path / 'items').mkdir()
    (tmp_path / 'crew/worker.json').write_text(json.dumps({'role': 'worker', 'pane': '%5'}))
    for row in [HIGH, LOW]:
        (tmp_path / f"items/{row['id']}.json").write_text(json.dumps(row))


def test_real_go_dispatch_warns_and_still_succeeds(tmp_path, monkeypatch, capsys):
    _store(tmp_path)
    monkeypatch.setattr(cli, 'Tmux', lambda *a, **k: NullPanes(screen=''))
    monkeypatch.setattr(cli.entity_suggest, 'suggest', lambda *a: None)
    rc = cli.main(['--root', str(tmp_path), '--backend', 'files', 'go', 'low', 'worker', '--reason', 'specialized work'])
    out = capsys.readouterr()
    assert rc == 0
    assert 'higher-priority' in out.err and 'high (P1)' in out.err
    assert 'note: specialized work' in out.out
    assert json.loads((tmp_path / 'items/low.json').read_text())['status'] == 'in_progress'
    assert json.loads((tmp_path / 'items/high.json').read_text())['status'] == 'open'


def test_real_crew_names_waiter_for_live_lower_priority_work(tmp_path, monkeypatch, capsys):
    _store(tmp_path)
    (tmp_path / 'items/low.json').write_text(json.dumps(LOW | {'status': 'in_progress', 'assignee': 'worker'}))
    monkeypatch.setattr(cli, 'Tmux', lambda *a, **k: NullPanes(screen=''))
    assert cli.main(['--root', str(tmp_path), '--backend', 'files', 'crew', '--local']) == 0
    out = capsys.readouterr().out
    assert 'higher-priority' in out and 'high (P1)' in out


def test_snapshot_excludes_open_dependency_and_retains_labels(tmp_path):
    _store(tmp_path)
    high = HIGH | {'labels': ['custom'], 'dependencies': [{'id': 'low', 'dependency_type': 'blocks'}]}
    (tmp_path / 'items/high.json').write_text(json.dumps(high))
    ready, _ = cli._priority_snapshot(SimpleNamespace(root=tmp_path, backend='files'))
    assert [r['id'] for r in ready] == ['low']


def test_failed_snapshot_is_unknown_without_refusing_dispatch(monkeypatch):
    def fail(_):
        raise OSError('tracker down')
    monkeypatch.setattr(cli, '_priority_snapshot', fail)
    assert 'unavailable' in cli._priority_go_note(SimpleNamespace(item='low'))


def test_fleet_does_not_treat_parked_or_record_rows_as_work():
    from shantytown.answer import Answer
    from shantytown.protocols import Agent
    reg = SimpleNamespace(all=lambda: Answer.complete_read(
        [Agent(name="worker", pane="p")], how="test registry"))
    panes = SimpleNamespace(exists=lambda p: True)
    active = [LOW | {"assignee": "worker", "labels": ["parked:by-design"]},
              LOW | {"assignee": "worker", "title": "inbox: message"}]
    assert pa.fleet_advice([HIGH], active, reg, panes) == []
    assert len(pa.fleet_advice([HIGH], [LOW | {"assignee": "worker"}] * 3, reg, panes)) == 1


def test_liveness_exception_is_not_a_down_owner():
    from shantytown.answer import Answer
    from shantytown.protocols import Agent
    reg = SimpleNamespace(all=lambda: Answer.complete_read(
        [Agent(name="unknown", pane="p")], how="test registry"))
    def failed(_):
        raise OSError("tmux unavailable")
    down = pa.down_agents(reg, SimpleNamespace(exists=failed))
    assert down == set()
    assert pa.advice([HIGH | {"assignee": "unknown"}], LOW, down) == ""


def test_dispatch_advisory_uses_the_deployments_pane_adapter(monkeypatch):
    from shantytown.answer import Answer
    from shantytown.protocols import Agent
    reg = SimpleNamespace(all=lambda: Answer.complete_read(
        [Agent(name="live", pane="p")], how="test registry"))
    monkeypatch.setattr(cli, "_priority_snapshot", lambda a: ([HIGH | {"assignee": "live"}], [LOW]))
    monkeypatch.setattr(cli, "_registry", lambda a: reg)
    monkeypatch.setattr(cli, "_panes", lambda a: SimpleNamespace(exists=lambda p: True))
    def bare_tmux(*a, **kw):
        raise AssertionError("bare tmux must not classify deployment owners")
    monkeypatch.setattr(cli, "Tmux", bare_tmux)
    assert cli._priority_go_note(SimpleNamespace(item="low")) == ""
