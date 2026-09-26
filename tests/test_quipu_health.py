"""Exercise the real HTTP boundary with a local server; never a production store."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from shantytown import quipu_health as health


@pytest.mark.parametrize('status,body,token,state,code', [
    (400, '{"error":"invalid episode JSON: missing field `name`"}', True, 'ok', 0),
    (401, '{}', False, 'no-token', 1),
    (401, '{}', True, 'bad-token', 1),
    (403, '{"reason":"server_is_read_only"}', True, 'read-only', 1),
    (403, '{}', True, 'unknown', 2),
    (400, '{"error":"episode failed"}', True, 'unknown', 2),
    (422, 'missing field', True, 'unknown', 2),
    (200, '{}', True, 'unknown', 2),
    (502, 'secret-value', True, 'unknown', 2),
])
def test_classify(status, body, token, state, code):
    result = health.classify(status, body, token)
    assert (result.state, result.code) == (state, code)
    assert 'secret-value' not in result.render()


def test_real_transport_reads_token_file_each_request_and_never_follows_redirect(tmp_path, monkeypatch):
    token = tmp_path / 'token'
    monkeypatch.delenv('QUIPU_AUTH_TOKEN', raising=False)
    monkeypatch.setenv('QUIPU_AUTH_TOKEN_FILE', str(token))
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            seen.append((self.path, self.headers.get('Authorization'), body))
            if self.path == '/redirect/episode':
                self.send_response(307)
                self.send_header('Location', '/episode')
                self.end_headers()
                return
            status = 400 if self.headers.get('Authorization') == 'Bearer accepted' else 401
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'invalid episode JSON: missing field `name`'}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    try:
        assert health.check(url).state == 'no-token'
        token.write_text('wrong')
        assert health.check(url).state == 'bad-token'
        token.write_text('accepted\n')
        assert health.check(url).state == 'ok'
        assert health.check(url + '/redirect').state == 'unknown'
        assert len(seen) == 4
        assert all(row[2] == b'{}' for row in seen)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert health.check(url, timeout=.1).state == 'unreachable'


def test_missing_and_unsafe_url():
    assert health.check(None).state == 'unconfigured'
    assert health.check('https://user:secret@example.com').state == 'unconfigured'


@pytest.mark.parametrize('state,code', [('ok', 0), ('bad-token', 1), ('unknown', 2)])
def test_doctor_renders_probe_and_propagates_exit(tmp_path, monkeypatch, capsys, state, code):
    from shantytown import cli, doctor
    from types import SimpleNamespace

    spec = next(s for s in doctor.SPECS if s.name == 'quipu')
    monkeypatch.setattr(doctor, 'detect_all', lambda *a, **k: [
        doctor.Health(spec, True, '1.0', None, None, None, True)
    ])
    monkeypatch.setattr(cli, '_socket_check', lambda a: ('ok', 'fixture'))
    monkeypatch.setattr(cli, '_fold_socket', lambda c, *a: c)
    monkeypatch.setattr(cli, '_registry', lambda a: (_ for _ in ()).throw(RuntimeError('fixture')))
    monkeypatch.setattr(cli.guard_mod, 'discover', lambda: [])
    monkeypatch.setenv('QUIPU_SERVER', 'http://example.test')
    seen = []
    def probe(server):
        seen.append(server)
        return health.WriteHealth(state, 'fixture result', code)
    monkeypatch.setattr(health, 'check', probe)
    args = SimpleNamespace(tool='quipu', install=False, dry_run=False, relay=False,
                           no_latest=True, root=str(tmp_path), backend='files')
    assert cli._cmd_doctor(args) == code
    assert seen == ['http://example.test']
    assert f'{state}: fixture result' in capsys.readouterr().out
