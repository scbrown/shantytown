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
import subprocess

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
            if path.exists() and (path.stat().st_mode & 0o077 or path.stat().st_size > 8_000_000):
                raise Refused('registry must be private and bounded')
            data = json.loads(path.read_text()) if path.exists() else {
                'version': 1, 'board': self.board, 'bindings': {}}
            if (not isinstance(data, dict) or data.get('version') != 1
                    or data.get('board') != self.board
                    or not isinstance(data.get('bindings'), dict)):
                raise Refused('registry identity or schema mismatch')
            if len(data['bindings']) > 10000:
                raise Refused('registry binding bound reached')
            for key, record in data['bindings'].items():
                if (not isinstance(record, dict) or not isinstance(record.get('repo'), str)
                        or not re.fullmatch(r'[\w.-]+/[\w.-]+', record['repo'])
                        or type(record.get('number')) is not int or record['number'] < 1
                        or key != f"{record['repo']}#{record['number']}"
                        or not isinstance(record.get('bead'), str) or not BEAD.fullmatch(record['bead'])
                        or not isinstance(record.get('author'), str) or not record['author']
                        or not isinstance(record.get('head'), str) or not SHA.fullmatch(record['head'])
                        or not isinstance(record.get('done'), list)
                        or any(not isinstance(d, str) or not re.fullmatch(r'[0-9a-f]{64}', d)
                               for d in record['done'])):
                    raise Refused('registry binding schema unreadable')
                pending = record.get('pending')
                if pending is not None and (not isinstance(pending, dict)
                        or not isinstance(pending.get('digest'), str)
                        or not re.fullmatch(r'[0-9a-f]{64}', pending['digest'])
                        or pending.get('state') not in ('closed', 'deferred')
                        or not isinstance(pending.get('reason'), str)):
                    raise Refused('registry pending intent unreadable')
            creates = data.get('creates', {})
            if not isinstance(creates, dict):
                raise Refused('registry creation journal unreadable')
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
            record = old
        data['bindings'][key] = record
        # Explicit verified registration also reconciles a lost create response.
        creates = data.get('creates', {})
        for intent_key, intent in list(creates.items()):
            if not isinstance(intent, dict):
                raise Refused('creation intent unreadable')
            if all(intent.get(k) == record[k] for k in ('repo','bead','author','head')):
                del creates[intent_key]
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

class GitHubAdapter:
    """CLI adapter; complete bounded comment reads, no merge operation."""
    def __init__(self):
        import subprocess
        self.subprocess = subprocess

    def api(self,path,body=None,method=None):
        argv=['gh','api','--method',method or ('GET' if body is None else 'POST'),path]
        if body is not None: argv += ['--input','-']
        result=self.subprocess.run(argv,input=json.dumps(body) if body is not None else None,
                                   capture_output=True,text=True,timeout=45)
        if result.returncode: raise Refused('forge response unavailable')
        try:return json.loads(result.stdout)
        except ValueError as error:raise Refused('forge returned invalid JSON') from error

    def read(self,repo,number):
        value=self.api(f'repos/{repo}/pulls/{number}')
        try:
            return {'head':value['head']['sha'],'body':value['body'],
                    'state':value['state'],'draft':value['draft'],'node_id':value['node_id']}
        except (KeyError,TypeError) as error:raise Refused('unknown PR response shape') from error

    def comments(self,repo,number):
        from .pr_preflight import pages
        # All comments are read; lifecycle markers are dedupe keys, not signatures.
        rows=pages(self.api,f'repos/{repo}/issues/{number}/comments')
        if any(not isinstance(row,dict) or not isinstance(row.get('body'),str) for row in rows):
            raise Refused('comment inventory unreadable')
        return [row['body'] for row in rows]

    def comment(self,repo,number,text):
        self.api(f'repos/{repo}/issues/{number}/comments',{'body':text})

    def close(self,repo,number):
        self.api(f'repos/{repo}/pulls/{number}',{'state':'closed'},'PATCH')

    def draft(self,repo,number):
        value=self.read(repo,number)
        node=value.get('node_id')
        if not isinstance(node,str) or not node:raise Refused('PR node identity missing')
        self.api('graphql',{'query':'mutation($id:ID!){convertPullRequestToDraft(input:{pullRequestId:$id}){pullRequest{id isDraft}}}',
                            'variables':{'id':node}})


class SeedsReader:
    """Read only known IDs through the explicit reviewed remote board."""
    def __init__(self,server,graph):
        import subprocess
        self.subprocess=subprocess
        self.argv=['sd','--quipu',server,'--graph',graph]
        where=self.command('where','--json')
        if (not isinstance(where,dict) or where.get('mode')!='remote'
                or where.get('graph')!=graph or where.get('quipu_url')!=server):
            raise Refused('selected Seeds route is unproven')

    def command(self,*args):
        value=self.subprocess.run([*self.argv,*args],capture_output=True,text=True,timeout=45)
        if value.returncode:raise Refused('selected Seeds read unavailable')
        try:return json.loads(value.stdout)
        except ValueError as error:raise Refused('selected Seeds response unreadable') from error

    def __call__(self,bead):
        if not BEAD.fullmatch(bead):raise Refused('invalid known bead ID')
        item=self.command('show',bead,'--json')
        if isinstance(item,list):
            if len(item)!=1:raise Refused('known bead response ambiguous')
            item=item[0]
        return item


def create_bound(registry, repo, checkout, bead, author, board, read_bead, forge,
                 title, body, decisions, *, base="main", draft=False, dry_run=False):
    """Prove ownership before creation; never replay a pending create intent."""
    from . import pr_preflight
    if board != registry.board or not BEAD.fullmatch(bead):
        raise Refused("board or primary bead mismatch")
    item = read_bead(bead)
    if (not isinstance(item, dict) or item.get("id") != bead
            or item.get("assignee") != author
            or item.get("status") not in ("open", "in_progress", "deferred")):
        raise Refused("creation requires an owned open bead")
    if item["status"] == "deferred" and not draft:
        raise Refused("deferred bead requires a draft PR")
    candidate = pr_preflight.candidate(checkout, repo, base, forge.api)
    intent_key = hashlib.sha256(f"{repo}:{candidate['head_ref']}".encode()).hexdigest()
    with registry.locked() as data:
        intents = data.setdefault("creates", {})
        if intent_key in intents:
            raise Indeterminate("pending create must be reconciled by explicit registration")
        if not dry_run:
            intents[intent_key] = {"repo": repo, "bead": bead, "author": author,
                                   "head": candidate["head"], "branch": candidate["head_ref"]}
            registry.save(data)
        try:
            result = pr_preflight.run(checkout, repo, base, bead, body, title, decisions,
                                      create=True, draft=draft, dry_run=dry_run, api=forge.api)
        except Indeterminate:
            raise
        except Refused:
            if not dry_run:
                del intents[intent_key]; registry.save(data)
            raise
    if dry_run:
        # The overlap gate previews the body; do not leak private body to logs.
        return {"outcome": "would_create", "bead": bead, "head": candidate["head"]}
    try:
        register(registry, repo, result["number"], author, board, read_bead, forge)
        with registry.locked() as data:
            data["creates"].pop(intent_key, None)
            registry.save(data)
    except Exception as error:
        raise Indeterminate("PR created; binding outcome pending, inspect before retry") from error
    return {"created": result["created"], "number": result["number"], "bead": bead}


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description='Explicit PR/bead binding; no merge authority')
    parser.add_argument('--registry',required=True)
    parser.add_argument('--quipu',required=True)
    parser.add_argument('--graph',required=True)
    modes=parser.add_subparsers(dest='mode',required=True)
    register_parser=modes.add_parser('register')
    register_parser.add_argument('--repo',required=True)
    register_parser.add_argument('--pr',required=True,type=int)
    register_parser.add_argument('--author',required=True)
    create_parser=modes.add_parser('create')
    create_parser.add_argument('--repo',required=True)
    create_parser.add_argument('--checkout',required=True,type=Path)
    create_parser.add_argument('--bead',required=True)
    create_parser.add_argument('--author',required=True)
    create_parser.add_argument('--title',required=True)
    create_parser.add_argument('--body-file',required=True,type=Path)
    create_parser.add_argument('--dispositions-file',required=True,type=Path)
    create_parser.add_argument('--base',default='main')
    create_parser.add_argument('--draft',action='store_true')
    create_parser.add_argument('--dry-run',action='store_true')
    modes.add_parser('reconcile')
    args=parser.parse_args(argv)
    try:
        reader=SeedsReader(args.quipu,args.graph)
        registry=Registry(args.registry,args.graph)
        forge=GitHubAdapter()
        if args.mode=='register':
            record=register(registry,args.repo,args.pr,args.author,args.graph,reader,forge)
            result={key:record[key] for key in ('repo','number','bead','author','head')}
        elif args.mode=='create':
            result=create_bound(registry,args.repo,args.checkout,args.bead,args.author,
                                args.graph,reader,forge,args.title,args.body_file.read_text(),
                                json.loads(args.dispositions_file.read_text()),
                                base=args.base,draft=args.draft,dry_run=args.dry_run)
        else:result=reconcile(registry,args.graph,reader,forge)
        print(json.dumps({'outcome':'verified','result':result},sort_keys=True))
        return 0
    except (Refused,OSError,ValueError,TimeoutError,subprocess.SubprocessError) as error:
        # Private reason remains in journal, not command output or public comments.
        print(json.dumps({'outcome':'indeterminate' if isinstance(error,Indeterminate) else 'refused',
                          'message':'binding or lifecycle outcome unverified; inspect private journal'}))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
