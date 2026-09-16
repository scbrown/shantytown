"""Task bindings and cost projections. Camayoc owns every token calculation."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime


def epoch(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def bindings(root, rig, records, agents=None):
    """Read paired session-local starts, clipped by next start and real closes."""
    path = Path(root) / 'stats.sqlite'
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute('SELECT * FROM task_contexts ORDER BY agent,session,ts,id').fetchall()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        ends = conn.execute('SELECT * FROM cost_focus_ends').fetchall() if 'cost_focus_ends' in tables else []
    result = []
    for i, row in enumerate(rows):
        if (agents is not None and row["agent"] not in agents) or not row['paired_start']:
            continue
        end, reason = None, 'open'
        if i + 1 < len(rows) and (rows[i+1]['agent'], rows[i+1]['session']) == (row['agent'], row['session']):
            end, reason = rows[i+1]['ts'], 'next declaration'
        candidates = [(r['ts'], r['reason']) for r in ends if r['session'] == row['session']
                      and r['agent'] == row['agent'] and r['task'] == row['task'] and r['ts'] >= row['ts']]
        record = records.get(row['task'])
        if record and record.get('closed_at'):
            closed = epoch(record['closed_at'])
            # A previous close cannot terminate an explicitly reopened focus.
            if closed >= row['ts']:
                candidates.append((closed, 'tracker close'))
        for stamp, why in candidates:
            if end is None or stamp < end:
                end, reason = stamp, why
        result.append({'schema_version': 1, 'rig': rig, 'bead': row['task'],
                       'agent': row['agent'], 'session': row['session'],
                       'interval': str(row['id']), 'start': row['ts'], 'end': end,
                       'boundary_reason': reason, 'evidence': 'st task_contexts paired_start'})
    return result


def capture_end(conn, payload, now, agent):
    """Only confirmed, standalone defer closes a focus through this hook.

    Direct br closes are clipped using public tracker read-back at retrieval.
    Compound shell commands cannot prove which mutation succeeded.
    """
    import shlex
    if payload.get('hook_event_name') != 'PostToolUse' or payload.get('tool_name') != 'Bash':
        return
    try:
        args = shlex.split((payload.get('tool_input') or {}).get('command', ''))
    except (ValueError, TypeError):
        return
    response = json.dumps(payload.get('tool_response', ''))
    if len(args) < 4 or Path(args[0]).name != 'st' or args[1] != 'defer' or 'deferred as' not in response:
        return
    if any(a in {';', '&&', '||', '|'} for a in args):
        return
    session = payload.get('session_id')
    if not session:
        return
    conn.execute('CREATE TABLE IF NOT EXISTS cost_focus_ends '
                 '(session TEXT,agent TEXT,task TEXT,ts REAL,reason TEXT,call_id TEXT, '
                 'UNIQUE(session,agent,call_id))')
    conn.execute('INSERT OR IGNORE INTO cost_focus_ends VALUES (?,?,?,?,?,?)',
                 (session, agent, args[2], now, 'confirmed defer',
                  payload.get('tool_use_id') or payload.get('tool_call_id') or str(now)))


def _br(config, *args):
    response = subprocess.run([config.get('br', 'br'), *args], cwd=config['tracker_repo'],
                              capture_output=True, text=True, timeout=30)
    if response.returncode:
        raise RuntimeError(response.stderr[:300] or 'tracker command failed')
    return json.loads(response.stdout)


def show(config, bead):
    result = _br(config, 'show', bead, '--json')
    if isinstance(result, list):
        result = result[0] if len(result) == 1 else None
    if not isinstance(result, dict) or result.get('id') != bead:
        raise ValueError('tracker read did not return the requested item')
    return result


def discover_sources(root, since_h=24):
    """Use existing harness/card path resolvers; discovery never reads counts."""
    from .files import FilesRegistry
    from .harness import for_card
    from .stats import _claude_project_dir, _codex_cwd
    from .cli import _default_settings
    cards = FilesRegistry(Path(root) / 'crew').all().exact()
    settings = _default_settings(Path(root))
    cutoff = time.time() - since_h * 3600
    sources = []
    for card in cards:
        fmt = for_card(card, root=root).name
        if fmt == 'claude':
            paths = (Path.home() / '.claude/projects' / _claude_project_dir(card.workspace)).glob('*.jsonl')
        elif fmt == 'codex' and settings(card):
            paths = (Path(settings(card)).parent / 'sessions').glob('*/*/*/*.jsonl')
        else:
            continue
        for path in paths:
            if path.stat().st_mtime < cutoff:
                continue
            if fmt == 'codex' and _codex_cwd(path) != str(Path(card.workspace)):
                continue
            sources.append({'agent': card.name, 'harness': fmt, 'path': str(path)})
    return sources


def retrieve(root, config):
    # Read the metadata first to discover only the tracker IDs actually needed.
    sources = config.get('sources')
    if sources is None:
        sources = discover_sources(root, config.get('since_hours', 24))
    agents = {source['agent'] for source in sources}
    provisional = bindings(root, config['rig'], {}, agents)
    records = {bead: show(config, bead) for bead in sorted({b['bead'] for b in provisional})}
    method = {'system': 'session_usage', 'query': 'work_cost', 'params': {
        'bindings': bindings(root, config['rig'], records, agents), 'sources': sources}}
    # The method file contains source paths and bindings, never transcript bodies.
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json') as handle:
        json.dump(method, handle); handle.flush()
        response = subprocess.run([sys.executable, config['retrieve_script'], handle.name],
                                  capture_output=True, text=True, timeout=120)
    if response.returncode:
        raise RuntimeError(response.stderr[:300] or response.stdout[:300])
    payload = json.loads(response.stdout)
    if payload.get('status') != 'retrieved':
        raise ValueError('Camayoc could not retrieve costs')
    return payload['result'], records, method


def receipt(result, bead):
    groups = [g for g in result['groups'] if g['bead'] == bead]
    if not groups:
        return None
    body = {'scope': result['scope'], 'groups': groups}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    lines = [f'cost receipt: {digest}', f"Scope: {result['scope']}. Provisional; later transcript flushes may revise this receipt."]
    for g in groups:
        counts = '/'.join(str(g['counts'][k]) if g['counts'][k] is not None else 'UNKNOWN'
                          for k in ('input_uncached', 'output', 'cache_read_input', 'cache_write_input'))
        lines.append(f"cost: {counts} tokens (uncached in/out/cache read/cache write), model {g['model'] or 'UNKNOWN'}, "
                     f"harness {g['harness']}, agent {g['agent']}, sessions {len(g['sessions'])}, coverage {g['coverage']}")
    return digest, '\n'.join(lines)


def _atomic_json(path, value):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
        json.dump(value, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = handle.name
    os.replace(temporary, path)


def comment_closed(config, result, records, pending_path):
    pending = json.loads(pending_path.read_text()) if pending_path.exists() else {}
    posted = []
    for bead, record in records.items():
        if record.get('status') != 'closed':
            continue
        value = receipt(result, bead)
        if not value:
            continue
        digest, body = value
        prior = pending.get(bead)
        if prior:
            digest, body = prior['digest'], prior['body']
        current = show(config, bead)
        # A reopened item must not receive a closure receipt.
        if current.get('status') != 'closed':
            continue
        if digest in json.dumps(current.get('comments', [])):
            if prior:
                del pending[bead]
                _atomic_json(pending_path, pending)
            continue
        if prior:
            # Two successful absent reads separated in time before a SAME-BODY
            # retry. A failed read above never advances this evidence counter.
            if time.time() - prior.get('last_read', 0) < 5:
                continue
            prior['absent_reads'] = prior.get('absent_reads', 0) + 1
            prior['last_read'] = time.time()
            _atomic_json(pending_path, pending)
            if prior['absent_reads'] < 2:
                continue
        pending[bead] = {'digest': digest, 'body': body, 'absent_reads': 0, 'last_read': time.time()}
        _atomic_json(pending_path, pending)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md') as handle:
            handle.write(body); handle.flush()
            _br(config, 'comments', 'add', bead, '--file', handle.name, '--json')
        if digest not in json.dumps(show(config, bead).get('comments', [])):
            raise RuntimeError(f'{bead}: cost write indeterminate; verify before retry')
        del pending[bead]
        _atomic_json(pending_path, pending)
        posted.append(bead)
    return posted


def metrics(result):
    # Corrected allocations can decrease, so these absolute totals are gauges.
    lines = ['# TYPE st_bead_tokens_total gauge', '# TYPE st_bead_cost_coverage gauge',
             '# TYPE st_bead_cost_last_success_timestamp_seconds gauge']
    for group in result['groups']:
        labels = {k: group[k] or 'UNKNOWN' for k in ('rig', 'bead', 'agent', 'harness', 'model')}
        label = ','.join(f'{k}={json.dumps(str(v))}' for k, v in labels.items())
        for kind, count in group['counts'].items():
            if count is not None:
                lines.append(f'st_bead_tokens_total{{{label},kind="{kind}"}} {count}')
        lines.append(f'st_bead_cost_coverage{{{label}}} {int(group["coverage"] == "observed")}')
    lines.append(f'st_bead_cost_last_success_timestamp_seconds {time.time()}')
    return '\n'.join(lines) + '\n'


def run(args):
    try:
        config = json.loads((Path(args.root) / 'cost.json').read_text())
        lock_path = Path(args.root) / 'cost.lock'
        with lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result, records, method = retrieve(args.root, config)
            if args.sync:
                # Do not publish a clean-looking replacement on a failed source.
                if result['errors']:
                    raise RuntimeError('Camayoc source coverage UNKNOWN: ' + '; '.join(result['errors']))
                destination = Path(config['metric_path'])
                with tempfile.NamedTemporaryFile(mode='w', dir=destination.parent, delete=False) as handle:
                    handle.write(metrics(result)); temporary = handle.name
                os.replace(temporary, destination)
                if config.get('metrics_script'):
                    pushed = subprocess.run([sys.executable, config['metrics_script'], str(destination),
                                             '--rig', config['rig']], capture_output=True, text=True, timeout=30)
                    if pushed.returncode:
                        raise RuntimeError('cost metric publication failed')
                if config.get('publish_script'):
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.json') as handle:
                        json.dump(method, handle); handle.flush()
                        published = subprocess.run([sys.executable, config['publish_script'], handle.name,
                            '--actor', 'st-cost', '--state', str(Path(args.root) / 'cost-graph-state.json'),
                            '--post', '--publish-status'], capture_output=True, text=True, timeout=120)
                    if published.returncode:
                        raise RuntimeError('Camayoc cost graph publication failed: ' + published.stderr[:300])
                posted = comment_closed(config, result, records, Path(args.root) / 'cost-pending.json')
                print(json.dumps({'receipt': result['receipt'], 'commented': posted}))
            elif args.bead:
                value = receipt(result, args.bead)
                print(json.dumps(result, indent=2) if args.json else value[1] if value else 'cost: UNKNOWN (no attributable requests)')
            else:
                print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f'cost: UNKNOWN: {exc}', file=sys.stderr)
        return 2
