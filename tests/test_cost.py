import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from shantytown import cost, task_order


def database(tmp_path):
    conn = sqlite3.connect(tmp_path / 'stats.sqlite')
    conn.executescript(task_order._SCHEMA)
    for stamp, task, paired in [(10, 'p-a', 1), (20, 'p-b', 1), (30, 'p-c', 0)]:
        conn.execute('INSERT INTO task_contexts(ts,agent,session,task,paired_start) VALUES (?,?,?,?,?)',
                     (stamp, 'worker', 'session', task, paired))
    conn.commit()
    return conn


def test_focus_boundaries_closed_and_unpaired_next(tmp_path):
    conn = database(tmp_path); conn.close()
    records = {'p-a': {'closed_at': '1970-01-01T00:00:15Z'}}
    result = cost.bindings(tmp_path, 'project', records)
    assert [(r['bead'], r['start'], r['end']) for r in result] == [('p-a', 10, 15), ('p-b', 20, 30)]
    assert cost.bindings(tmp_path, 'project', {}, {'other'}) == []


def sample():
    return {'scope': 'supplied sources, not lifetime', 'groups': [{
        'rig': 'project', 'bead': 'p-a', 'agent': 'worker', 'harness': 'codex',
        'model': None, 'counts': {'input_uncached': None, 'cache_read_input': 40,
                                'cache_write_input': None, 'output': 2},
        'sessions': ['session'], 'coverage': 'UNKNOWN'}]}


def test_unknown_is_omitted_not_published_as_zero():
    text = cost.metrics(sample())
    assert 'kind="input_uncached"' not in text
    assert 'kind="cache_write_input"' not in text
    assert 'kind="cache_read_input"} 40' in text
    assert 'st_bead_cost_coverage{' in text and 'model="UNKNOWN"} 0' in text
    assert 'UNKNOWN/2/40/UNKNOWN' in cost.receipt(sample(), 'p-a')[1]


def test_indeterminate_write_is_not_blind_retried(tmp_path):
    pending = tmp_path / 'pending.json'
    current = {'id': 'p-a', 'status': 'closed', 'comments': []}
    with patch.object(cost, 'show', return_value=current), patch.object(cost, '_br', side_effect=TimeoutError):
        with pytest.raises(TimeoutError):
            cost.comment_closed({}, sample(), {'p-a': current}, pending)
    original = json.loads(pending.read_text())['p-a']['body']
    with patch.object(cost, 'show', return_value=current), patch.object(cost, '_br') as writer:
        cost.comment_closed({}, sample(), {'p-a': current}, pending)
        writer.assert_not_called()
    with patch.object(cost, 'time') as clock, patch.object(cost, 'show', return_value=current), patch.object(cost, '_br') as writer:
        clock.time.return_value = 10**12
        cost.comment_closed({}, sample(), {'p-a': current}, pending)
        writer.assert_not_called()
        assert json.loads(pending.read_text())['p-a']['absent_reads'] == 1
    digest = json.loads(pending.read_text())['p-a']['digest']
    with patch.object(cost, 'show', return_value={**current, 'comments': [{'text': digest}]}), patch.object(cost, '_br') as writer:
        cost.comment_closed({}, sample(), {'p-a': current}, pending)
        writer.assert_not_called()
        assert json.loads(pending.read_text()) == {}
    assert original.startswith('cost receipt: ')


def test_reopened_item_gets_no_closure_comment(tmp_path):
    with patch.object(cost, 'show', return_value={'status': 'open'}), patch.object(cost, '_br') as writer:
        assert cost.comment_closed({}, sample(), {'p-a': {'status': 'closed'}}, tmp_path/'pending') == []
        writer.assert_not_called()


def test_confirmed_defer_clips_focus_and_redeclaration_reopens(tmp_path):
    conn = database(tmp_path)
    payload = {'hook_event_name': 'PostToolUse', 'tool_name': 'Bash', 'session_id': 'session',
               'tool_use_id': 'defer1', 'tool_input': {'command': 'st work defer p-c human --reason-file reason'},
               'tool_response': {'stdout': 'p-c deferred as blocked:human', 'exit_code': 0}}
    task_order.capture(conn, payload, 35, 'worker')
    start = {**payload, 'tool_use_id': 'begin2', 'tool_input': {'command': 'st agent stats --begin-task p-c'}}
    task_order.capture(conn, {**start, 'hook_event_name': 'PreToolUse'}, 40, 'worker')
    task_order.capture(conn, {**start, 'tool_response': {'stdout': task_order.marker('p-c')}}, 41, 'worker')
    conn.commit(); conn.close()
    focus = cost.bindings(tmp_path, 'project', {})
    assert focus[-1]['bead'] == 'p-c' and focus[-1]['start'] == 41


def test_active_projection_replaces_cached_pause_with_new_sample(tmp_path, monkeypatch):
    path = tmp_path / 'cost.prom'
    path.write_text('st_bead_cost_paused_for_review 1\n')
    monkeypatch.setattr(cost.time, 'time', lambda: 123)
    cost._publish_metrics({'metric_path': str(path)}, sample())
    text = path.read_text()
    assert 'st_bead_cost_paused_for_review 0\n' in text
    assert 'st_bead_cost_last_success_timestamp_seconds 123\n' in text
    assert 'st_bead_cost_sync_last_run_timestamp_seconds 123\n' in text
    assert 'kind="cache_read_input"} 40\n' in text
