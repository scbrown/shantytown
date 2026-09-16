"""Bounded, scheduled-only source rotation; Camayoc still owns all counts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import statistics

TARGET = 20


def _identity(path):
    # Read metadata only. A large message or malformed source is not permission
    # to infer identity from a recent filename or the agent's role directory.
    with path.open() as handle:
        for _ in range(32):
            line = handle.readline(1024 * 1024)
            if not line or not line.endswith('\n'):
                break
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if row.get('type') == 'session_meta':
                value = row.get('payload') or {}
                return value.get('id'), value.get('cwd')
            if row.get('sessionId') and row.get('cwd'):
                return row['sessionId'], row['cwd']
    return None, None


def active_sources(root):
    """Require a live pane, recent stats session, and matching source metadata."""
    from .files import FilesRegistry
    from .harness import for_card
    from .session_budget import current_session
    from .cli import _default_settings
    from .tmux import Tmux
    root = Path(root)
    panes, settings = Tmux(), _default_settings(root)
    sources = []
    for card in FilesRegistry(root / 'crew').all().exact():
        if not card.pane or not card.workspace or not panes.exists(card.pane):
            continue
        session = current_session(root, card.name)
        if not session or not re.fullmatch(r'[A-Za-z0-9-]+', session):
            continue
        fmt = for_card(card, root=root).name
        if fmt == 'claude':
            # Project slugs are lossy (underscores become hyphens too). Match
            # exact session filenames, then prove the workspace from metadata.
            paths = (Path.home() / '.claude/projects').glob(f'*/{session}.jsonl')
        elif fmt == 'codex':
            state = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
            homes = [state / 'shantytown/codex' / card.name / 'sessions']
            if settings(card):
                homes.append(Path(settings(card)).parent / 'sessions')
            paths = [p for home in homes for p in home.glob(f'*/*/*/*{session}.jsonl')]
        else:
            continue
        matches = {}
        for path in paths:
            try:
                if _identity(path) == (session, str(Path(card.workspace))):
                    matches[str(path.resolve())] = path.resolve()
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        if len(matches) == 1:
            sources.append({'agent': card.name, 'harness': fmt,
                            'path': str(next(iter(matches.values()))), 'session': session})
    return sorted(sources, key=lambda s: (s['agent'], s['session']))


def state_path(root):
    # Separate population: never erase the pinned pilot's history or cooldown.
    return Path(root) / 'cost-active-graph-state.json'


def _load(path):
    return json.loads(path.read_text()) if path.exists() else {}


def review(root):
    history = _load(state_path(root).with_suffix('.budget.json'))
    samples = history.get('distinct_samples', [])
    if len(samples) < TARGET:
        return None
    return {'status': 'REVIEW_DUE', 'population': 'active-current-sessions',
            'distinct_shapes': len(samples), 'source_scope': 'supplied sessions, not fleet tail proof',
            'dimensions': {key: {'min': min(s[key] for s in samples),
                                'median': statistics.median(s[key] for s in samples),
                                'max': max(s[key] for s in samples)}
                           for key in ('items', 'records', 'body_bytes')},
            'samples': samples,
            'sources': _load(Path(root) / 'cost-active-rotation.json').get('samples', []),
            'last_publication_status': _load(state_path(root).with_suffix('.receipt.json')).get('status', 'UNKNOWN')}


def select(root):
    sources = active_sources(root)
    if not sources:
        raise ValueError('no metadata-verified active transcript source')
    prior = _load(Path(root) / 'cost-active-rotation.json').get('last', ['', ''])
    source = next((s for s in sources if [s['agent'], s['session']] > prior), sources[0])
    return source


def attempted(root, source, since):
    """Advance even on refusal so an oversized source cannot starve its peers."""
    from .cost import _atomic_json
    path = Path(root) / 'cost-active-rotation.json'
    state = _load(path)
    state['last'] = [source['agent'], source['session']]
    receipt = _load(state_path(root).with_suffix('.receipt.json'))
    evidence = state.setdefault('samples', [])
    known = {s['attempted_at'] for s in evidence}
    # Preserve provenance for each actual distinct preflight observation; no
    # selection, no-op, refused budget or backoff counts as a new sample.
    for sample in receipt.get('budget_history', {}).get('distinct_samples', []):
        stamp = sample['attempted_at']
        if stamp >= since and stamp == receipt.get('attempted_at') and stamp not in known:
            evidence.append({**sample, 'source': source})
    _atomic_json(path, state)
