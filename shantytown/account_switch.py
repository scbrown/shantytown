"""Durable, non-secret account transitions served through the existing cycle."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
import uuid

from .files import write_json_atomic


def choice(card):
    return {key: getattr(card, key, None)
            for key in ('account', 'harness', 'model', 'auto_failover')}


def binding(account):
    # Bind the reference, not its value. Changing config cannot redirect an old
    # queued transition to another login or default model.
    value = [account.name, account.harness, account.model, account.credential_ref]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


class Requests:
    def __init__(self, root):
        self.root = Path(root) / 'governor' / 'account-switches'

    def get(self, agent):
        if not agent or Path(agent).name != agent or agent in {".", ".."}:
            raise ValueError("unsafe agent name")
        try:
            value = json.loads((self.root / (agent + '.json')).read_text())
        except FileNotFoundError:
            return None
        if not isinstance(value, dict) or value.get('agent') != agent:
            raise ValueError('account transition state is invalid')
        return value

    def put(self, value):
        self.get(value['agent'])
        self.root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.root / (value['agent'] + '.json'), value)

    def request(self, card, target, *, model=None, auto_failover=None,
                automatic=False, source=None):
        old = self.get(card.name)
        desired = dict(account=target.name, harness=target.harness,
                       model=model or target.model,
                       auto_failover=(card.auto_failover if auto_failover is None
                                      else auto_failover))
        if old and old['desired'] == desired and old['binding'] == binding(target):
            return old
        value = dict(agent=card.name, id=uuid.uuid4().hex, source=source,
                     expected=choice(card), desired=desired, binding=binding(target),
                     automatic=automatic, requested_at=time.time(), phase='queued',
                     notified=False)
        self.put(value)
        return value

    def clear(self, agent):
        (self.root / (agent + '.json')).unlink(missing_ok=True)


def checkpoint(request):
    return ('account switch ' + request['id'] + ': ' + str(request.get('source') or 'legacy')
            + ' -> ' + request['desired']['account'])
