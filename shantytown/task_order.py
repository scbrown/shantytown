"""Metadata-only ordering within explicitly declared, session-local task scopes.

A scope is a declaration, not proof that it covers every action on a dispatch.
Missing starts, ambiguous commands and unbound calls never become passing tasks.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
import sqlite3

_TASK = re.compile(r'[A-Za-z][A-Za-z0-9_]*-[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z')
_READS = frozenset('mcp__homelab__quipu_' + name for name in ('search', 'query', 'ask'))
_MATERIAL = frozenset(('Edit', 'Write', 'MultiEdit', 'apply_patch'))
_READ_ONLY = frozenset(('Read', 'Glob', 'Grep', 'LS', 'ToolSearch', 'view_image',
                        'clockcurr_time', 'clocksleep'))
_SCHEMA = '''
CREATE TABLE IF NOT EXISTS task_contexts (
 id INTEGER PRIMARY KEY, ts REAL NOT NULL, agent TEXT NOT NULL,
 session TEXT NOT NULL, task TEXT NOT NULL, paired_start INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS task_context_session ON task_contexts(session, id);
CREATE TABLE IF NOT EXISTS task_order_events (
 id INTEGER PRIMARY KEY, ts REAL NOT NULL, agent TEXT NOT NULL,
 session TEXT NOT NULL, context_id INTEGER, task TEXT,
 kind TEXT NOT NULL, tool TEXT NOT NULL, call_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS task_order_context ON task_order_events(context_id, ts);
'''


def valid_task(value):
    return isinstance(value, str) and _TASK.fullmatch(value) is not None


def boundary_task(payload):
    """Only a standalone command declares a boundary; a compound could act first."""
    if payload.get('tool_name') != 'Bash':
        return None
    try:
        args = shlex.split((payload.get('tool_input') or {}).get('command', ''))
    except (ValueError, TypeError, AttributeError):
        return None
    if (len(args) == 4 and Path(args[0]).name == 'st'
            and args[1:3] == ['stats', '--begin-task'] and valid_task(args[3])):
        return args[3]
    return None


def marker(task):
    return f'task boundary requested: {task}'


def failed(payload):
    if payload.get('hook_event_name') == 'PostToolUseFailure':
        return True
    if payload.get('tool_response') is None:
        return True
    response = payload.get('tool_response')
    if isinstance(response, dict):
        if response.get('is_error') or response.get('isError') or response.get('error'):
            return True
        for key in ('exit_code', 'exitCode', 'returncode'):
            if response.get(key) not in (None, 0):
                return True
    return bool(payload.get('is_error'))


def capture(conn, payload, now, agent):
    event = payload.get('hook_event_name')
    if event not in ('PreToolUse', 'PostToolUse', 'PostToolUseFailure'):
        return
    conn.executescript(_SCHEMA)
    session = payload.get('session_id') or ''
    tool = payload.get('tool_name') or ''
    call_id = payload.get('tool_use_id') or payload.get('tool_call_id') or ''
    if not isinstance(session, str) or not isinstance(call_id, str):
        return
    # Never borrow another process's task from an environment or an agent plate.
    bound = conn.execute('SELECT id,task FROM task_contexts WHERE session=? AND agent=? '
                         'ORDER BY id DESC LIMIT 1', (session, agent)).fetchone() if session else None
    context, task = bound if bound else (None, None)

    def add(kind, explicit_task=task, context_id=context):
        if call_id and conn.execute(
                'SELECT 1 FROM task_order_events WHERE session=? AND agent=? '
                'AND tool=? AND call_id=? AND kind=? LIMIT 1',
                (session, agent, tool, call_id, kind)).fetchone():
            return
        conn.execute('INSERT INTO task_order_events '
                     '(ts,agent,session,context_id,task,kind,tool,call_id) '
                     'VALUES (?,?,?,?,?,?,?,?)',
                     (now, agent, session, context_id, explicit_task, kind, tool, call_id))

    boundary = boundary_task(payload)
    if boundary:
        if event == 'PreToolUse':
            add('boundary_start', boundary, None)
        elif (event == 'PostToolUse' and session and not failed(payload)
              and marker(boundary) in json.dumps(payload.get('tool_response', ''))):
            start = conn.execute('SELECT id FROM task_order_events WHERE session=? '
                                 'AND agent=? AND kind=? AND call_id=? AND task=? LIMIT 1',
                                 (session, agent, 'boundary_start', call_id, boundary)).fetchone() if call_id else None
            # Repeating the current declaration must not erase earlier actions.
            if task == boundary:
                return
            conn.execute('INSERT INTO task_contexts(ts,agent,session,task,paired_start) '
                         'VALUES (?,?,?,?,?)', (now, agent, session, boundary, bool(start)))
        return

    inputs = payload.get('tool_input') or {}
    explicit = inputs.get('task') if isinstance(inputs, dict) else None
    if tool in _READS:
        if event != 'PreToolUse':
            # A task-specific read can be recorded unbound, but cannot be silently
            # attached to whichever task a later concurrent call made current.
            matches = valid_task(explicit) and explicit == task
            add('read_failed' if failed(payload) else 'read_completed',
                explicit if valid_task(explicit) else None, context if matches else None)
        return
    if tool in _READ_ONLY:
        return
    kind = 'material_start' if tool in _MATERIAL else 'ambiguous_start'
    if event == 'PreToolUse':
        add(kind)
    elif event in ('PostToolUse', 'PostToolUseFailure'):
        start = conn.execute('SELECT id FROM task_order_events WHERE session=? AND agent=? '
                             'AND context_id IS ? AND tool=? AND call_id=? '
                             'AND kind IN (?,?) LIMIT 1',
                             (session, agent, context, tool, call_id,
                              'material_start', 'ambiguous_start')).fetchone() if call_id else None
        if not start:
            add('missing_start')


def capture_fault(conn, payload, now, agent):
    """If capture partly fails, poison the current scope when the store is usable."""
    session = payload.get('session_id')
    if not isinstance(session, str) or not session:
        return
    bound = conn.execute('SELECT id,task FROM task_contexts WHERE session=? AND agent=? '
                         'ORDER BY id DESC LIMIT 1', (session, agent)).fetchone()
    if bound:
        conn.execute('INSERT INTO task_order_events '
                     '(ts,agent,session,context_id,task,kind,tool,call_id) '
                     'VALUES (?,?,?,?,?,?,?,?)',
                     (now, agent, session, bound[0], bound[1], 'capture_error', '', ''))


def report(root, since_epoch=0, agent=None):
    """Read without creating/migrating a database; missing evidence is UNKNOWN."""
    p = Path(root) / 'stats.sqlite'
    base = {'scope': 'explicit session-local task declarations; not all dispatches',
            'contexts': [], 'unbound_events': 0, 'status': 'UNKNOWN'}
    if not p.exists():
        return base
    with sqlite3.connect(f'file:{p}?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'task_contexts', 'task_order_events'} <= tables:
            return base
        contexts = conn.execute('SELECT * FROM task_contexts WHERE ts>=? ORDER BY ts',
                                (since_epoch,)).fetchall()
        base['unbound_events'] = conn.execute(
            'SELECT count(*) FROM task_order_events WHERE ts>=? AND context_id IS NULL '
            'AND kind!=? AND (? IS NULL OR agent=?)',
            (since_epoch, 'boundary_start', agent, agent)).fetchone()[0]
        for ctx in contexts:
            if agent and ctx['agent'] != agent:
                continue
            rows = conn.execute('SELECT ts,kind FROM task_order_events WHERE context_id=? '
                                'ORDER BY ts', (ctx['id'],)).fetchall()
            reads = [r['ts'] for r in rows if r['kind'] == 'read_completed']
            material = [r['ts'] for r in rows if r['kind'] == 'material_start']
            ambiguous = [r['ts'] for r in rows if r['kind'] == 'ambiguous_start']
            missing = any(r['kind'] in ('missing_start', 'capture_error') for r in rows)
            verdict, reason = 'UNKNOWN', 'no definite material action observed'
            if not ctx['paired_start'] or missing:
                reason = 'capture incomplete or start unpaired'
            elif not reads:
                reason = 'no successful task-matched MCP read observed'
            elif material:
                if ambiguous and min(ambiguous) <= min(reads):
                    reason = 'an earlier operation may have acted or read outside this instrument'
                elif min(reads) < min(material):
                    verdict, reason = 'PASS', 'read completed before first definite action start'
                else:
                    verdict, reason = 'FAIL', 'definite action started before first recorded read completed'
            base['contexts'].append({**dict(ctx), 'verdict': verdict, 'reason': reason,
                                     'reads': len(reads), 'first_read': min(reads) if reads else None,
                                     'first_material_action': min(material) if material else None})
        base['status'] = 'OBSERVED' if base['contexts'] else 'UNKNOWN'
    return base


def instruction(task):
    """Name the explicit declaration on dispatch and autonomous haul paths."""
    if not valid_task(task):
        return ''
    return (f"Before task work, run `st stats --begin-task {task}` as a standalone "
            f"tool command, then query Quipu with task={task}. ")
