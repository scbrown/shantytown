"""Ordering requires completed reads and action starts, never scrape timestamps."""
import sqlite3

import pytest

from shantytown import stats, task_order


@pytest.fixture
def record(tmp_path, monkeypatch):
    monkeypatch.setenv('SHANTY_AGENT', 'worker')
    monkeypatch.setattr(stats, '_maybe_export', lambda *args: None)
    monkeypatch.setattr(stats, '_safe_scrub_transcript', lambda *args, **kw: None)
    clock = iter(range(1, 100))
    monkeypatch.setattr(stats.time, 'time', lambda: next(clock))

    def emit(tool, event='PostToolUse', session='s1', call='c', response=None, **inputs):
        payload = dict(tool_name=tool, hook_event_name=event, session_id=session,
                       tool_use_id=call, tool_input=inputs)
        if response is not None:
            payload['tool_response'] = response
        stats.capture(tmp_path, payload)
    return emit


def boundary(record, task='test-1', session='s1', paired=True):
    command = f'st stats --begin-task {task}'
    if paired:
        record('Bash', 'PreToolUse', session, call=task, command=command)
    record('Bash', session=session, call=task, command=command,
           response={'stdout': task_order.marker(task), 'exit_code': 0})


def read(record, task='test-1', session='s1', response=None):
    record('mcp__homelab__quipu_search', session=session, call='read', task=task,
           query='SECRET-QUERY-MUST-NOT-BE-PERSISTED',
           response=response if response is not None else {'content': []})


def action(record, session='s1', paired=True):
    if paired:
        record('Edit', 'PreToolUse', session, call='edit', file_path='/tmp/example')
    record('Edit', session=session, call='edit', response={}, file_path='/tmp/example')


def verdict(tmp_path):
    return task_order.report(tmp_path)['contexts'][0]['verdict']


def test_read_before_action_pass_and_metadata_only(record, tmp_path):
    boundary(record)
    read(record)
    action(record)
    assert verdict(tmp_path) == 'PASS'
    conn = sqlite3.connect(tmp_path / 'stats.sqlite')
    rows = conn.execute('SELECT * FROM task_order_events').fetchall()
    assert 'SECRET' not in repr(rows)
    # Pre events must not double the existing usage denominator.
    assert conn.execute('SELECT count(*) FROM events').fetchone()[0] == 3


def test_read_started_before_action_but_completed_after_fails(record, tmp_path):
    boundary(record)
    record('mcp__homelab__quipu_search', 'PreToolUse', task='test-1')
    action(record)
    read(record)
    assert verdict(tmp_path) == 'FAIL'


@pytest.mark.parametrize('failure', [{'isError': True}, {'error': 'no access'},
                                      {'exit_code': 1}])
def test_failed_read_never_passes(record, tmp_path, failure):
    boundary(record)
    read(record, response=failure)
    action(record)
    assert verdict(tmp_path) == 'UNKNOWN'


def test_absent_response_never_passes(record, tmp_path):
    boundary(record)
    record('mcp__homelab__quipu_search', task='test-1')
    action(record)
    assert verdict(tmp_path) == 'UNKNOWN'


@pytest.mark.parametrize('bound_paired,action_paired', [(False, True), (True, False)])
def test_missing_starts_unknown(record, tmp_path, bound_paired, action_paired):
    boundary(record, paired=bound_paired)
    read(record)
    action(record, paired=action_paired)
    assert verdict(tmp_path) == 'UNKNOWN'


def test_ambiguous_shell_before_read_unknown(record, tmp_path):
    boundary(record)
    record('Bash', 'PreToolUse', command='possibly-mutating-command')
    read(record)
    action(record)
    assert verdict(tmp_path) == 'UNKNOWN'


def test_concurrent_sessions_never_borrow_tasks(record, tmp_path):
    boundary(record, 'test-1', 's1')
    boundary(record, 'test-2', 's2')
    read(record, 'test-1', 's2')
    action(record, 's2')
    read(record, 'test-1', 's1')
    action(record, 's1')
    result = task_order.report(tmp_path)
    assert [c['verdict'] for c in result['contexts']] == ['PASS', 'UNKNOWN']
    assert result['unbound_events'] == 1


def test_redeclaration_cannot_hide_first_action(record, tmp_path):
    boundary(record)
    action(record)
    boundary(record)
    read(record)
    result = task_order.report(tmp_path)
    assert len(result['contexts']) == 1
    assert verdict(tmp_path) == 'FAIL'


def test_duplicate_events_do_not_inflate_reads(record, tmp_path):
    boundary(record)
    read(record)
    read(record)
    action(record)
    assert task_order.report(tmp_path)['contexts'][0]['reads'] == 1


def test_failure_hook_records_no_legacy_success(record, tmp_path):
    boundary(record)
    record('mcp__homelab__quipu_search', 'PostToolUseFailure', task='test-1')
    action(record)
    assert verdict(tmp_path) == 'UNKNOWN'
    conn = sqlite3.connect(tmp_path / 'stats.sqlite')
    assert conn.execute('SELECT count(*) FROM events').fetchone()[0] == 2


@pytest.mark.parametrize('command', ['st stats --begin-task test-1 && touch x',
                                    'echo st stats --begin-task test-1',
                                    'st stats --begin-task bad/id'])
def test_no_compound_or_invalid_boundaries(record, tmp_path, command):
    record('Bash', command=command, response={'stdout': task_order.marker('test-1')})
    assert not task_order.report(tmp_path)['contexts']


def test_empty_report_does_not_create_store(tmp_path):
    assert task_order.report(tmp_path)['status'] == 'UNKNOWN'
    assert not (tmp_path / 'stats.sqlite').exists()


def test_optional_capture_error_preserves_legacy(record, tmp_path, monkeypatch):
    def broken(*args):
        raise RuntimeError('sensitive content')
    monkeypatch.setattr(task_order, 'capture', broken)
    action(record)
    conn = sqlite3.connect(tmp_path / 'stats.sqlite')
    assert conn.execute('SELECT count(*) FROM events').fetchone()[0] == 1


def test_boundary_cli_does_not_guess_session(tmp_path, capsys):
    from shantytown.cli import main
    assert main(['--root', str(tmp_path), 'stats', '--begin-task', 'test-1']) == 0
    assert task_order.marker('test-1') in capsys.readouterr().out
    assert not task_order.report(tmp_path)['contexts']
    assert main(['--root', str(tmp_path), 'stats', '--task-order']) == 0
    assert 'UNKNOWN' in capsys.readouterr().out


def test_haul_prompts_name_task_boundary():
    from shantytown.feed_check import haul_feed_message, haul_resume_message
    assert 'st stats --begin-task test-1' in haul_feed_message('test-1', 'work', 0)
    assert 'st stats --begin-task test-2' in haul_resume_message('test-2', 'work')


def test_partial_capture_failure_cannot_leave_a_passing_scope(record, tmp_path, monkeypatch):
    boundary(record)
    read(record)
    action(record)
    assert verdict(tmp_path) == 'PASS'
    def broken(*args):
        raise RuntimeError('sensitive content')
    monkeypatch.setattr(task_order, 'capture', broken)
    record('Bash', 'PreToolUse', call='broken')
    assert verdict(tmp_path) == 'UNKNOWN'
