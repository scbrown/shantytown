"""Design handoffs to the executive; scheduling remains the recipient's decision."""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path

from .protocols import Agent
from .attribution import attribute


class Refused(ValueError):
    pass


def executive(cards: list[Agent]) -> Agent:
    marked = [c for c in cards if not c.retired and 'executive' in c.effective_roles()]
    if len(marked) != 1:
        raise Refused(f'expected exactly one executive administrator; found {len(marked)}')
    target = marked[0]
    if target.role != 'administrator':
        raise Refused(f'{target.name}: executive must also be an administrator')
    return target


def design(tracker, item: str) -> dict:
    if not item or any(c in item for c in ('/', '\\', '\n')) or item.startswith('-'):
        raise Refused('invalid bead identifier')
    from .br import BrTracker
    from .files import FilesTracker
    if isinstance(tracker, BrTracker):
        result = tracker._bd_for(item, 'show', item, '--json')
        if result.returncode:
            raise RuntimeError(f'cannot read design {item}: {result.stderr.strip()[:200]}')
        value = json.loads(result.stdout)
        row = value[0] if isinstance(value, list) and len(value) == 1 else value
    elif isinstance(tracker, FilesTracker):
        row = json.loads(tracker._path(item).read_text())
    else:
        raise Refused('sling requires the br or files tracker')
    if not isinstance(row, dict) or not row.get('title'):
        raise RuntimeError('incomplete design response')
    if isinstance(tracker, BrTracker) and row.get('id') != item:
        raise RuntimeError('design response identity mismatch')
    if not any(isinstance(row.get(k), str) and row[k].strip()
               for k in ('description', 'design')):
        raise Refused(f'{item} has no description or design body; bead the design first')
    children = sorted({r['id'] for r in row.get('dependents', [])
                       if r.get('dependency_type') == 'parent-child'})
    if isinstance(tracker, FilesTracker):
        children = sorted({p.stem for p in tracker.root.glob('*.json')
                           if json.loads(p.read_text()).get('parent') == item})
    return dict(item=item, title=row['title'], description=row.get('description', ''),
                design=row.get('design', ''), children=children)


def prepare(body: dict, sender: str, target: Agent, note: str) -> dict:
    event = dict(kind='design_handoff', sender=sender, executive=target.name,
                 host=target.host, **body, note=note)
    digest = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()[:24]
    marker = f'sling:{digest}'
    # All prose belongs on the design bead. Even a multi-page note has a bounded
    # notification. The marker is first so ReceiptInbox can find closed receipts.
    payload = marker + ' ' + attribute(
        f'Design {body["item"]} for scheduling; {len(body["children"])} children. '
        'Read the sling handoff comment on the bead.', sender)
    if len(('inbox: ' + payload).encode()) > 500:
        raise Refused('sender or bead identifier exceeds the durable inbox byte budget')
    return dict(event, marker=marker, payload=payload)


def _comments(tracker, item):
    from .br import BrTracker, comments
    if isinstance(tracker, BrTracker):
        return comments(tracker, item)
    return json.loads(tracker._path(item).read_text()).get('comments', [])


def _comment(tracker, item, body):
    from .br import BrTracker, append_comment
    from .files import write_json_atomic
    if isinstance(tracker, BrTracker):
        append_comment(tracker, item, body)
    else:
        path = tracker._path(item)
        row = json.loads(path.read_text())
        row.setdefault('comments', []).append({'text': body})
        write_json_atomic(path, row)


def deliver(plan: dict, tracker, box, root: Path):
    """Serialize retries and verify comments and receipts, including closed ones.

    The same content has the same marker. An indeterminate create is retried by
    read-back, never by inventing a new notification or timestamp.
    """
    directory = root / 'sling'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / (plan['marker'] + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_path = directory / (plan['marker'] + '.json')
        from .files import write_json_atomic
        if state_path.exists():
            event = json.loads(state_path.read_text())
        else:
            event = dict(plan, at=datetime.now(timezone.utc).isoformat())
            write_json_atomic(state_path, event)
        comment = (plan['marker'] + '\n' + json.dumps(dict(plan, at=event['at']), sort_keys=True, ensure_ascii=False))
        def recorded():
            return any(c.get('text', c.get('body')) == comment
                       for c in _comments(tracker, plan['item']))
        if not recorded():
            _comment(tracker, plan['item'], comment)
        if not recorded():
            raise RuntimeError('handoff comment read-back missing; no notification sent')
        receipt = box.find_delivery(plan['executive'], plan['marker'])
        if receipt is None:
            box.deliver(plan['executive'], plan['payload'], frm=plan['sender'])
            receipt = box.find_delivery(plan['executive'], plan['marker'])
        if receipt is None or receipt.body != plan['payload']:
            raise RuntimeError('durable receipt read-back missing or mismatched; inspect before retrying')
        event['receipt'] = receipt.id
        event['state'] = 'delivered'
        # One queryable event file per handoff, updated atomically on retries.
        write_json_atomic(state_path, event)
        return receipt
