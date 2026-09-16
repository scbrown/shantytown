import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shantytown import cost, cost_sampling as sampling
from shantytown.protocols import Agent


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + '\n')


def test_discovery_proves_current_identity_not_slug_or_newest_file(tmp_path, monkeypatch):
    from shantytown import files, harness, cli, session_budget, tmux
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    cards = [Agent(name=n, role='worker', workspace='/workspace/has_underscore/' + n, pane=n)
             for n in ('claude', 'codex', 'down', 'stale', 'wrong')]
    monkeypatch.setattr(files.FilesRegistry, 'all', lambda _: SimpleNamespace(exact=lambda: cards))
    monkeypatch.setattr(tmux.Tmux, 'exists', lambda _, pane: pane != 'down')
    monkeypatch.setattr(session_budget, 'current_session', lambda _, n: None if n == 'stale' else n + '-session')
    monkeypatch.setattr(harness, 'for_card', lambda c, **_: SimpleNamespace(name='codex' if c.name == 'codex' else 'claude'))
    monkeypatch.setattr(cli, '_default_settings', lambda _: lambda c: str(tmp_path / 'role/config.toml'))
    for card in cards:
        if card.name == 'codex':
            path = tmp_path / 'state/shantytown/codex/codex/sessions/2026/09/16/rollout-codex-session.jsonl'
            write(path, {'type': 'session_meta', 'payload': {'id': 'codex-session', 'cwd': card.workspace}})
        else:
            path = tmp_path / '.claude/projects/lossy-slug' / (card.name + '-session.jsonl')
            write(path, {'sessionId': card.name + '-session', 'cwd': 'wrong' if card.name == 'wrong' else card.workspace})
    got = sampling.active_sources(tmp_path)
    assert [s['agent'] for s in got] == ['claude', 'codex']
    # Two distinct matching paths are ambiguous, not permission to choose newest.
    duplicate = tmp_path / '.claude/projects/another/claude-session.jsonl'
    write(duplicate, {'sessionId': 'claude-session', 'cwd': cards[0].workspace})
    assert [s['agent'] for s in sampling.active_sources(tmp_path)] == ['codex']


def test_rotation_and_failed_attempt_do_not_fabricate_samples(tmp_path, monkeypatch):
    sources = [{'agent': n, 'session': 'current', 'harness': 'codex', 'path': n} for n in ('a', 'b')]
    monkeypatch.setattr(sampling, 'active_sources', lambda _: sources)
    assert sampling.select(tmp_path) == sources[0]
    sampling.attempted(tmp_path, sources[0], 100)
    assert sampling.select(tmp_path) == sources[1]
    sampling.attempted(tmp_path, sources[1], 100)
    assert sampling.select(tmp_path) == sources[0]
    assert json.loads((tmp_path / 'cost-active-rotation.json').read_text())['samples'] == []
    monkeypatch.setattr(sampling, 'active_sources', lambda _: [])
    with pytest.raises(ValueError, match='no metadata-verified'):
        sampling.select(tmp_path)


def test_provenance_never_relabels_previous_receipt(tmp_path):
    sample = {'attempted_at': 101, 'items': 1, 'records': 3, 'body_bytes': 90}
    write(sampling.state_path(tmp_path).with_suffix('.receipt.json'), {
        'attempted_at': 101, 'budget_history': {'distinct_samples': [sample]}})
    a, b = {'agent': 'a', 'session': 'one'}, {'agent': 'b', 'session': 'two'}
    sampling.attempted(tmp_path, a, 100)
    sampling.attempted(tmp_path, b, 102)
    state = json.loads((tmp_path / 'cost-active-rotation.json').read_text())
    assert state['samples'] == [{**sample, 'source': a}]


def args(root, sync=True):
    return SimpleNamespace(root=root, sync=sync, bead=None, json=True)


def setup(root):
    write(root / 'cost.json', {'sample_active_sources': True, 'publish_script': 'publisher', 'sources': ['pilot']})


def test_display_never_selects_or_advances_population(tmp_path, monkeypatch):
    setup(tmp_path)
    def forbidden(*_):
        raise AssertionError('display entered sampler')
    monkeypatch.setattr(sampling, 'select', forbidden)
    monkeypatch.setattr(sampling, 'attempted', forbidden)
    monkeypatch.setattr(cost, '_run_selected', lambda a, c, s: 0 if c['sources'] == ['pilot'] else 9)
    assert cost.run(args(tmp_path, False)) == 0
    assert not (tmp_path / 'cost-active-rotation.json').exists()


@pytest.mark.parametrize('population', ['cost-graph-state', 'cost-active-graph-state'])
@pytest.mark.parametrize('condition', ['pending', 'cooldown'])
def test_population_change_cannot_bypass_pending_or_cooldown(tmp_path, monkeypatch, population, condition):
    setup(tmp_path)
    if condition == 'pending':
        write(tmp_path / (population + '.pending.json'), {})
    else:
        write(tmp_path / (population + '.receipt.json'), {'next_request_after': 10**12})
    def forbidden(*_):
        raise AssertionError('source read or write before safety check')
    monkeypatch.setattr(sampling, 'select', forbidden)
    monkeypatch.setattr(cost, '_run_selected', forbidden)
    assert cost.run(args(tmp_path)) == (2 if condition == 'pending' else 0)


def test_twentieth_shape_pauses_before_source_read_and_reports_distribution(tmp_path, monkeypatch, capsys):
    setup(tmp_path)
    samples = [{'items': 1, 'records': n, 'body_bytes': n * 10} for n in range(1, 21)]
    write(sampling.state_path(tmp_path).with_suffix('.budget.json'), {'distinct_samples': samples})
    def forbidden(*_):
        raise AssertionError('sampling continued past review boundary')
    monkeypatch.setattr(sampling, 'select', forbidden)
    monkeypatch.setattr(cost, '_run_selected', forbidden)
    calls = []
    def status_only(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(cost.subprocess, 'run', status_only)
    assert cost.run(args(tmp_path)) == 0
    assert len(calls) == 1 and '--review-only' in calls[0]
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'REVIEW_DUE'
    assert report['dimensions']['records'] == {'min': 1, 'median': 10.5, 'max': 20}
    assert json.loads((tmp_path / 'cost-active-review.json').read_text()) == report


def test_one_source_selected_and_failed_publication_advances(tmp_path, monkeypatch):
    setup(tmp_path)
    source = {'agent': 'a', 'session': 'current'}
    monkeypatch.setattr(sampling, 'select', lambda _: source)
    def refused(a, c, state):
        assert c['sources'] == [source]
        assert state == sampling.state_path(tmp_path)
        raise RuntimeError('budget refuses before network')
    monkeypatch.setattr(cost, '_run_selected', refused)
    assert cost.run(args(tmp_path)) == 2
    assert json.loads((tmp_path / 'cost-active-rotation.json').read_text()) == {
        'last': ['a', 'current'], 'samples': []}


@pytest.mark.parametrize('pending', [True, False])
def test_review_cannot_hide_pending_write_or_failed_health_publication(tmp_path, monkeypatch, pending):
    setup(tmp_path)
    samples = [{'items': 0, 'records': n, 'body_bytes': n * 10} for n in range(1, 21)]
    write(sampling.state_path(tmp_path).with_suffix('.budget.json'), {'distinct_samples': samples})
    if pending:
        write(sampling.state_path(tmp_path).with_suffix('.pending.json'), {})
    calls = []
    def failed(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=2, stderr='status unavailable')
    monkeypatch.setattr(cost.subprocess, 'run', failed)
    assert cost.run(args(tmp_path)) == 2
    assert len(calls) == (0 if pending else 1)
