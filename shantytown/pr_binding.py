"""Explicit crew PR/bead registry and reconciled lifecycle (aegis-62g).

No global PR discovery/adoption or merge authority. The caller supplies a fresh
selected-board reader, exact board identity and a forge adapter. API adapters and
scheduled activation are separate gates; this library is not live automation.
# arming: library -- caller must opt in each binding explicitly.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .pr_preflight import BEAD, Refused, Indeterminate

SHA = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')


def primary(body):
    if not isinstance(body, str):
        raise Refused('PR body is unreadable')
    markers = re.findall(r'^Bead:\s*(\S+)\s*$', body, re.MULTILINE)
    if len(markers) != 1 or not BEAD.fullmatch(markers[0]):
        raise Refused('PR requires exactly one primary Bead: marker')
    return markers[0]


class Registry:
    """Lock-protected private registry. Journal intent before every external write."""
    def __init__(self, root, board):
        self.root = Path(root)
        if not isinstance(board, str) or not board:
            raise Refused('explicit reviewed board identity required')
        self.board = board
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or self.root.stat().st_mode & 0o077:
            raise Refused('registry directory must be private and not a symlink')

    @contextmanager
    def locked(self):
        lockpath = self.root / 'lock'
        fd = os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.root / 'registry.json'
            if path.is_symlink():
                raise Refused('registry symlink refused')
            data = json.loads(path.read_text()) if path.exists() else {
                'version': 1, 'board': self.board, 'bindings': {}}
            if (not isinstance(data, dict) or data.get('version') != 1
                    or data.get('board') != self.board
                    or not isinstance(data.get('bindings'), dict)):
                raise Refused('registry identity or schema mismatch')
            yield data

    def save(self, data):
        fd, name = tempfile.mkstemp(dir=self.root, prefix='.registry-')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(data, stream, sort_keys=True)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(name, self.root / 'registry.json')
            directory = os.open(self.root, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if os.path.exists(name): os.unlink(name)


def checked_pull(forge, repo, number, bead, head=None):
    pull = forge.read(repo, number)
    if not isinstance(pull, dict) or type(pull.get('draft')) is not bool:
        raise Refused('unknown PR response shape')
    actual = pull.get('head')
    if not isinstance(actual, str) or not SHA.fullmatch(actual):
        raise Refused('unknown PR head')
    if primary(pull.get('body')) != bead or (head and actual != head):
        raise Refused('PR primary binding or exact head changed')
    if pull.get('state') not in ('open', 'closed'):
        raise Refused('unknown PR state')
    return pull


def register(registry, repo, number, actor, board, read_bead, forge):
    if board != registry.board or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
        raise Refused('board or repository identity mismatch')
    if type(number) is not int or number < 1 or not actor:
        raise Refused('invalid PR number or author of record')
    with registry.locked() as data:
        pull = forge.read(repo, number)
        if not isinstance(pull, dict): raise Refused('unknown PR response shape')
        bead = primary(pull.get('body'))
        pull = checked_pull(forge, repo, number, bead)
        item = read_bead(bead)
        if (not isinstance(item, dict) or item.get('id') != bead
                or item.get('assignee') != actor or item.get('status') == 'closed'):
            raise Refused('known open bead owned by author of record required')
        if item.get('status') not in ('open', 'in_progress', 'deferred'):
            raise Refused('unknown bead state')
        if pull['state'] != 'open' or (item['status'] == 'deferred' and not pull['draft']):
            raise Refused('new binding must match open/deferred state')
        key = f'{repo}#{number}'
        record = {'repo': repo, 'number': number, 'bead': bead, 'author': actor,
                  'head': pull['head'], 'pending': None, 'done': []}
        old = data['bindings'].get(key)
        if old:
            if any(old.get(k) != record[k] for k in ('repo','number','bead','author','head')):
                raise Refused('existing binding cannot be reassigned implicitly')
            return old
        data['bindings'][key] = record
        registry.save(data)
        return record


def reconcile(registry, board, read_bead, forge):
    """Re-read authoritative bead; incoming events only request this reconciliation.

    Forge adapter exposes read, comments, comment, draft, close. Reads normalize
    exact head/body/state/draft. Writes are followed by independent reads. Unknown
    responses leave a durable intent and raise Indeterminate; no in-call retry.
    """
    if board != registry.board: raise Refused('board identity mismatch')
    result = []
    with registry.locked() as data:
        for record in data['bindings'].values():
            item = read_bead(record['bead'])
            if (not isinstance(item, dict) or item.get('id') != record['bead']
                    or item.get('assignee') != record['author']):
                raise Refused('bead owner or authoritative read unknown')
            state = item.get('status')
            if state not in ('open','in_progress','deferred','closed'):
                raise Refused('unknown bead state')
            pending = record.get('pending')
            if state not in ('deferred','closed'):
                if pending: raise Refused('bead changed while lifecycle write is pending')
                continue
            reason = item.get('close_reason') if state == 'closed' else item.get('notes')
            if not isinstance(reason, str) or not reason.strip():
                raise Refused('lifecycle reason missing')
            identity = f"{record['repo']}:{record['number']}:{record['head']}:{state}:{reason}"
            digest = hashlib.sha256(identity.encode()).hexdigest()
            if pending and pending['digest'] != digest:
                raise Refused('bead changed while lifecycle write is pending')
            if digest in record['done']: continue
            pull = checked_pull(forge, record['repo'], record['number'],
                                record['bead'], record['head'])
            if not pending:
                record['pending'] = {'digest': digest, 'state': state, 'reason': reason}
                registry.save(data)
            marker = f'<!-- crew-bead-lifecycle:{digest} -->'
            # Private reason remains in the journal; do not publish arbitrary notes.
            text = f"Bead {record['bead']} is {state}.\n\n{marker}"
            try:
                comments = forge.comments(record['repo'], record['number'])
                if not isinstance(comments,list) or any(not isinstance(c,str) for c in comments):
                    raise Refused('comment coverage unknown')
                if not any(marker in comment for comment in comments):
                    forge.comment(record['repo'],record['number'],text)
                    comments = forge.comments(record['repo'],record['number'])
                    if not isinstance(comments,list) or not any(
                            isinstance(c,str) and marker in c for c in comments):
                        raise Refused('comment outcome unverified')
                # Read again after comment, including the bead, before state mutation.
                if read_bead(record['bead']) != item:
                    raise Refused('bead changed before PR state write')
                pull = checked_pull(forge,record['repo'],record['number'],
                                    record['bead'],record['head'])
                if state == 'closed' and pull['state'] != 'closed':
                    forge.close(record['repo'],record['number'])
                elif state == 'deferred' and not pull['draft']:
                    if pull['state'] != 'open': raise Refused('closed PR cannot be drafted')
                    forge.draft(record['repo'],record['number'])
                actual = checked_pull(forge,record['repo'],record['number'],
                                      record['bead'],record['head'])
                if ((state == 'closed' and actual['state'] != 'closed') or
                    (state == 'deferred' and not actual['draft'])):
                    raise Refused('lifecycle outcome unverified')
            except Exception as error:
                raise Indeterminate('lifecycle intent pending; reconcile before any retry') from error
            record['done'].append(digest)
            record['pending'] = None
            registry.save(data)
            result.append({'bead':record['bead'],'state':state,'digest':digest})
    return result
