"""Receipts must prove persistence, including after a lost reply or restart."""
from copy import deepcopy
from http.server import HTTPServer
import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from shantytown.files import FilesTracker, items
from shantytown.inbox import FilesInbox, TrackerInbox
from shantytown.inbox_bridge import Bridge, Conflict, handler, load_config

IDENTITY = dict(harness='creel', agent='tab-1', session='session-1',
                key_id='key-1', introducer='launcher', binding='binding-1')
PRINCIPAL = dict(token='test-token-' + 'x' * 32, identity=IDENTITY, recipients=['worker'])
ENVELOPE = dict(version='crew-handoff-v1', id='handoff-1', origin=IDENTITY,
                target=dict(harness='shantytown', agent='worker'),
                task=dict(id='task-1', pointer='https://example.org/tasks/1'),
                ownership=dict(lease_id='lease-1', owner=None), state='queued')


@pytest.fixture(params=['files', 'tracker'])
def bridge(request, tmp_path):
    if request.param == 'files':
        box = FilesInbox(tmp_path / 'inbox')
    else:
        tracker = FilesTracker(tmp_path / 'items')
        box = TrackerInbox(tracker, lambda: items(tracker), lambda: items(tracker))
    return Bridge(tmp_path / 'spool', box, request.param)


def test_restart_and_ack_keep_same_identity(bridge):
    r = bridge.deposit(ENVELOPE, PRINCIPAL)
    assert r['delivery'] == 'persisted' and not r['acknowledged']
    assert bridge.envelope(r['receipt_id']) == ENVELOPE
    assert len(bridge.inbox.unread('worker')) == 1
    assert bridge.status(r['receipt_id'], PRINCIPAL) == r
    bridge.inbox.mark_read('worker', [r['inbox_id']])
    restarted = Bridge(bridge.root, bridge.inbox, bridge.backend)
    ack = restarted.deposit(ENVELOPE, PRINCIPAL)
    assert ack == {**r, 'acknowledged': True}
    assert restarted.status(r['receipt_id'], PRINCIPAL) == ack
    assert bridge.inbox.unread('worker') == []


def test_lost_create_reply_recovers_without_duplicate(bridge, monkeypatch):
    original = bridge.inbox.deliver
    def lost(*a, **kw):
        original(*a, **kw)
        raise RuntimeError('reply lost after persistence')
    monkeypatch.setattr(bridge.inbox, 'deliver', lost)
    with pytest.raises(RuntimeError):
        bridge.deposit(ENVELOPE, PRINCIPAL)
    monkeypatch.setattr(bridge.inbox, 'deliver', original)
    r = Bridge(bridge.root, bridge.inbox, bridge.backend).deposit(ENVELOPE, PRINCIPAL)
    assert r['inbox_id'] == bridge.inbox.unread('worker')[0].id
    assert len(bridge.inbox.unread('worker')) == 1


def test_conflicting_reuse_never_overwrites(bridge):
    bridge.deposit(ENVELOPE, PRINCIPAL)
    changed = deepcopy(ENVELOPE)
    changed['task']['pointer'] += '/other'
    with pytest.raises(Conflict):
        bridge.deposit(changed, PRINCIPAL)
    assert len(bridge.inbox.unread('worker')) == 1


def test_origin_and_recipient_are_authenticated(bridge):
    for field in ['agent', 'session', 'key_id', 'introducer', 'binding']:
        forged = deepcopy(ENVELOPE)
        forged['origin'][field] += '-forged'
        with pytest.raises(PermissionError):
            bridge.deposit(forged, PRINCIPAL)
    wrong = deepcopy(ENVELOPE)
    wrong['target']['agent'] = 'someone-else'
    with pytest.raises(PermissionError):
        bridge.deposit(wrong, PRINCIPAL)
    assert not bridge.inbox.unread('worker')


def test_receipt_does_not_leak_to_other_principal(bridge):
    r = bridge.deposit(ENVELOPE, PRINCIPAL)
    other = deepcopy(PRINCIPAL)
    other['identity']['session'] = 'different'
    with pytest.raises(PermissionError):
        bridge.status(r['receipt_id'], other)


def test_readback_and_duplicates_fail_closed(bridge, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(bridge.inbox, 'find_delivery', lambda *a: None)
        with pytest.raises(RuntimeError, match='unproven'):
            bridge.deposit(ENVELOPE, PRINCIPAL)
    first = bridge.inbox.unread('worker')[0]
    bridge.inbox.deliver('worker', first.body)
    with pytest.raises(RuntimeError, match='ambiguous'):
        bridge.deposit(ENVELOPE, PRINCIPAL)


def test_pointer_byte_limit_before_write(bridge):
    large = deepcopy(ENVELOPE)
    large['task']['pointer'] = 'é' * 200
    with pytest.raises(ValueError, match='493-byte'):
        bridge.deposit(large, PRINCIPAL)
    assert not bridge.inbox.unread('worker')


def test_tracker_requires_all_status_reader(tmp_path):
    tracker = FilesTracker(tmp_path / 'items')
    with pytest.raises(RuntimeError, match='all-status'):
        TrackerInbox(tracker, lambda: items(tracker)).find_delivery('worker', 'handoff:x')


def test_http_auth_delivery_ack_and_cors(bridge):
    cfg = dict(origins=['https://example.org'], principals=[PRINCIPAL])
    server = HTTPServer(('127.0.0.1', 0), handler(bridge, cfg))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    def call(path, value=None, token=PRINCIPAL['token'], origin='https://example.org'):
        req = Request(base + path, data=json.dumps(value).encode() if value else None,
                      headers={'Authorization': 'Bearer ' + token, 'Origin': origin,
                               'Content-Type': 'application/json'})
        with urlopen(req, timeout=5) as response:
            assert response.headers['Access-Control-Allow-Origin'] == origin
            return json.load(response)
    try:
        for kw in [dict(token='wrong'), dict(origin='https://untrusted.example')]:
            with pytest.raises(HTTPError) as err:
                call('/handoffs', ENVELOPE, **kw)
            assert err.value.code == 403
        r = call('/handoffs', ENVELOPE)
        bridge.inbox.mark_read('worker', [r['inbox_id']])
        assert call('/handoffs/' + r['receipt_id']) == {**r, 'acknowledged': True}
        changed = deepcopy(ENVELOPE)
        changed['task']['pointer'] += '/changed'
        with pytest.raises(HTTPError) as err:
            call('/handoffs', changed)
        assert err.value.code == 409
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_private_config_validation(tmp_path):
    path = tmp_path / 'config.json'
    cfg = dict(origins=['https://example.org'], principals=[PRINCIPAL])
    path.write_text(json.dumps(cfg))
    assert load_config(path) == cfg
    cfg['origins'] = ['*']
    path.write_text(json.dumps(cfg))
    with pytest.raises(ValueError):
        load_config(path)


def test_br_receipts_request_untruncated_closed_rows():
    from subprocess import CompletedProcess
    from shantytown.br import items as br_items
    class Tracker:
        output = '[]'
        def _bd(self, *args):
            assert args == ('list', '--json', '--limit', '0', '--all')
            return CompletedProcess(args, 0, self.output, '')
    tracker = Tracker()
    assert br_items(tracker, include_closed=True) == []
    for malformed in ['', '{}', '[{"id":"x"}]']:
        tracker.output = malformed
        with pytest.raises(RuntimeError, match='incomplete'):
            br_items(tracker, include_closed=True)
