"""Optional authenticated HTTP transport for immutable crew handoff envelopes.

The spool retains payloads; the existing inbox remains the delivery/ack authority.
Run with ``python -m shantytown.inbox_bridge --help``. No background service is
installed, no browser storage is trusted, and reading a receipt changes no lease.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re
import sqlite3
from typing import Protocol
from urllib.parse import urlsplit

from .handoff import canonical_bytes, validate_identity
from .inbox import Message


class ReceiptInbox(Protocol):
    """Extra read seam, implemented by files and tracker inboxes."""
    def deliver(self, to: str, body: str, frm: str | None = None) -> Message: ...
    def find_delivery(self, me: str, marker: str) -> Message | None: ...


class Conflict(ValueError):
    pass


class Bridge:
    def __init__(self, root: Path, inbox: ReceiptInbox, backend: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.inbox, self.backend = inbox, backend
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS handoffs '
                       '(id TEXT PRIMARY KEY, envelope BLOB NOT NULL, body TEXT NOT NULL)')

    @contextmanager
    def _db(self):
        # Serialize recovery as well as creation: two retries must not both see
        # an absent inbox entry. This lock is shared by restarted bridge workers.
        with (self.root / 'bridge.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            db = sqlite3.connect(self.root / 'handoffs.sqlite')
            try:
                with db:
                    yield db
            finally:
                db.close()

    @staticmethod
    def authorize(envelope, principal):
        if envelope['origin'] != principal['identity']:
            raise PermissionError('origin does not match the authenticated principal')
        target = envelope['target']
        if (target['harness'] != 'shantytown'
                or target.get('agent') not in principal['recipients']):
            raise PermissionError('recipient is not authorized')

    def _receipt(self, rid, raw, body):
        envelope = json.loads(raw)
        msg = self.inbox.find_delivery(envelope['target']['agent'], 'handoff:' + rid)
        if msg is None or msg.body != body:
            raise RuntimeError('delivery is unproven: inbox entry missing or changed')
        return {'version': 'crew-inbox-receipt-v1', 'receipt_id': rid,
                'handoff_id': envelope['id'], 'task_id': envelope['task']['id'],
                'envelope_sha256': hashlib.sha256(raw).hexdigest(),
                'inbox_id': msg.id, 'backend': self.backend,
                'delivery': 'persisted', 'acknowledged': msg.read}

    def deposit(self, value, principal):
        raw = canonical_bytes(value)
        if len(raw) > 65536:
            raise ValueError('envelope exceeds 65536 bytes')
        envelope = json.loads(raw)
        self.authorize(envelope, principal)
        key = json.dumps([envelope['origin'], envelope['id']], sort_keys=True,
                         separators=(',', ':'), ensure_ascii=False).encode()
        rid = hashlib.sha256(key).hexdigest()
        pointer = envelope['task'].get('pointer')
        if not pointer:
            raise ValueError('durable handoff requires task.pointer')
        body = f"handoff:{rid} {hashlib.sha256(raw).hexdigest()} {pointer}"
        if len(body.encode()) > 493:
            raise ValueError('task pointer exceeds the 493-byte inbox budget')
        with self._db() as db:
            row = db.execute('SELECT envelope, body FROM handoffs WHERE id=?', (rid,)).fetchone()
            if row and row[0] != raw:
                raise Conflict('handoff ID already binds a different envelope')
            if not row:
                db.execute('INSERT INTO handoffs VALUES (?, ?, ?)', (rid, raw, body))
                # Payload must survive a crash AFTER inbox delivery. Commit it
                # before crossing the tracker process boundary, while locked.
                db.commit()
            msg = self.inbox.find_delivery(envelope['target']['agent'], 'handoff:' + rid)
            if msg is None:
                self.inbox.deliver(envelope['target']['agent'], body,
                                   frm=envelope['origin']['agent'])
            return self._receipt(rid, raw, body)

    def status(self, rid, principal):
        with self._db() as db:
            row = db.execute('SELECT envelope, body FROM handoffs WHERE id=?', (rid,)).fetchone()
            if not row:
                raise KeyError('unknown receipt')
            self.authorize(json.loads(row[0]), principal)
            return self._receipt(rid, *row)

    def envelope(self, rid):
        """Local operator read; HTTP only exposes the caller's receipts."""
        with self._db() as db:
            row = db.execute('SELECT envelope FROM handoffs WHERE id=?', (rid,)).fetchone()
            if not row:
                raise KeyError('unknown receipt')
            return json.loads(row[0])


def load_config(path):
    cfg = json.loads(Path(path).read_text())
    origins = cfg['origins']
    if not isinstance(origins, list) or not origins:
        raise ValueError('configure exact browser origins')
    for origin in origins:
        parsed = urlsplit(origin)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username or parsed.password or parsed.path
                or parsed.query or parsed.fragment):
            raise ValueError('origins must be exact HTTP(S) origins, without paths')
    principals = cfg['principals']
    if not isinstance(principals, list) or not principals:
        raise ValueError('configure authenticated principals')
    tokens = set()
    for p in principals:
        token = p['token']
        if (not isinstance(token, str) or len(token) < 32 or not token.isascii()
                or any(c.isspace() for c in token) or token in tokens):
            raise ValueError('each principal requires a unique ASCII bearer of at least 32 characters')
        tokens.add(token)
        p['identity'] = validate_identity(p['identity'])
        if p['identity']['harness'] != 'creel':
            raise ValueError('bridge origin must be creel')
        if (not isinstance(p['recipients'], list) or not p['recipients']
                or any(not isinstance(r, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', r)
                       for r in p['recipients'])):
            raise ValueError('configure explicit recipient names')
    return cfg


def handler(bridge, config):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *args):
            pass  # Request paths and credentials do not belong in access logs.

        def reply(self, code, value):
            data = json.dumps(value).encode()
            self.send_response(code)
            origin = self.headers.get('Origin')
            if origin in config['origins']:
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
            self.end_headers()
            self.wfile.write(data)

        def principal(self):
            origin = self.headers.get('Origin')
            if origin is not None and origin not in config['origins']:
                raise PermissionError('browser origin is not allowed')
            bearer = self.headers.get('Authorization', '')
            for p in config['principals']:
                if hmac.compare_digest(bearer.encode(), ('Bearer ' + p['token']).encode()):
                    return p
            raise PermissionError('authentication required')

        def do_OPTIONS(self):
            if self.headers.get('Origin') not in config['origins']:
                return self.reply(403, {'error': 'browser origin is not allowed'})
            self.reply(200, {})

        def do_POST(self):
            self.dispatch(True)

        def do_GET(self):
            self.dispatch(False)

        def dispatch(self, post):
            try:
                principal = self.principal()
                if post and self.path == '/handoffs':
                    if self.headers.get_content_type() != 'application/json':
                        raise ValueError('Content-Type must be application/json')
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 65536 or self.headers.get('Transfer-Encoding'):
                        raise ValueError('a 1..65536 byte Content-Length is required')
                    value = json.loads(self.rfile.read(size))
                    receipt = bridge.deposit(value, principal)
                elif not post and re.fullmatch(r'/handoffs/[0-9a-f]{64}', self.path):
                    receipt = bridge.status(self.path.rsplit('/', 1)[1], principal)
                else:
                    return self.reply(404, {'error': 'unknown route'})
                self.reply(200, receipt)
            except PermissionError as exc:
                self.reply(403, {'error': str(exc)})
            except Conflict as exc:
                self.reply(409, {'error': str(exc)})
            except KeyError:
                self.reply(404, {'error': 'unknown receipt'})
            except (ValueError, TypeError) as exc:
                self.reply(400, {'error': str(exc)})
            except Exception:
                # A tracker error can contain private paths or subprocess output.
                self.reply(503, {'error': 'delivery unproven; retry the identical envelope'})
    return Handler


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True, help='persistent bridge spool directory')
    p.add_argument('--config', type=Path, help='private principal and browser-origin configuration')
    p.add_argument('--repo', help='explicit br repository for the existing durable inbox')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8766)
    p.add_argument('--show', metavar='RECEIPT_ID', help='read an envelope locally without starting HTTP')
    args = p.parse_args(argv)
    if args.show:
        print(json.dumps(Bridge(args.root, None, 'br').envelope(args.show), indent=2))
        return
    if not args.config or not args.repo:
        p.error('--config and --repo are required when serving')
    from .br import BrTracker, items
    from .inbox import TrackerInbox
    tracker = BrTracker(repo=args.repo)
    inbox = TrackerInbox(tracker, lambda: items(tracker),
                         lambda: items(tracker, include_closed=True))
    bridge = Bridge(args.root, inbox, 'br')
    server = HTTPServer((args.host, args.port), handler(bridge, load_config(args.config)))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
