"""Real subprocess/session boundaries with fake servers and private homes."""
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from shantytown import quipu_auth


def test_resolver_and_shadowed_files(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('QUIPU_AUTH_TOKEN', raising=False)
    monkeypatch.delenv('QUIPU_AUTH_TOKEN_FILE', raising=False)
    legacy = tmp_path / '.config/aegis/quipu_token'
    legacy.parent.mkdir(parents=True)
    legacy.write_text('legacy')
    assert quipu_auth.token() == ''
    canonical = tmp_path / '.config/quipu/token'
    canonical.parent.mkdir()
    canonical.write_text('canonical\n')
    assert quipu_auth.token() == 'canonical'
    monkeypatch.setenv('QUIPU_AUTH_TOKEN', '  inline \n')
    monkeypatch.setenv('QUIPU_AUTH_TOKEN_FILE', str(legacy))
    assert quipu_auth.token() == 'inline'
    monkeypatch.setenv('QUIPU_AUTH_TOKEN', ' \n')
    assert quipu_auth.token() == 'legacy'
    monkeypatch.setenv('QUIPU_AUTH_TOKEN_FILE', str(tmp_path / 'absent'))
    assert quipu_auth.token() == ''


def test_real_registry_401_once_across_subprocesses(tmp_path):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            seen.append(self.path)
            self.send_response(200 if self.path == "/query" else 401)
            self.end_headers()
            self.wfile.write(b'{"rows": []}' if self.path == "/query" else b'fixture-response-secret')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    env = dict(os.environ, HOME=str(tmp_path), XDG_STATE_HOME=str(tmp_path / 'state'),
               QUIPU_AUTH_TOKEN='fixture-token', QUIPU_SESSION='fixture-one')
    code = '''
import sys
from shantytown.quipu import QuipuRegistry
try:
    QuipuRegistry(server=sys.argv[1], onto='http://fixture.example/ontology/')._knot('fixture')
except Exception as error:
    print(type(error).__name__)
'''
    def run(endpoint=url):
        return subprocess.run([sys.executable, '-c', code, endpoint], env=env,
                              capture_output=True, text=True, timeout=10)
    try:
        results = [run() for _ in range(3)]
        assert seen == ['/knot']
        assert sum('st: Quipu credential' in r.stderr for r in results) == 1
        assert all('fixture-response-secret' not in r.stderr + r.stdout for r in results)
        env['QUIPU_SESSION'] = 'fixture-two'
        assert run().returncode == 0
        assert len(seen) == 2
        assert run(url + '/other').returncode == 0
        assert len(seen) == 3
        env['QUIPU_SESSION'] = 'fixture-missing'
        env['QUIPU_AUTH_TOKEN'] = ''
        env['QUIPU_AUTH_TOKEN_FILE'] = str(tmp_path / 'absent')
        missing = [run() for _ in range(3)]
        assert len(seen) == 3
        assert sum('st: Quipu credential' in r.stderr for r in missing) == 1
        assert all('QuipuWriteRejected' in r.stdout for r in missing)
        read = subprocess.run([sys.executable, '-c',
            "from shantytown.quipu import QuipuRegistry; import sys; QuipuRegistry(server=sys.argv[1], onto='http://fixture.example/ontology/')._query('SELECT ?s WHERE {}')", url],
            env=env, capture_output=True, text=True, timeout=10)
        assert read.returncode == 0, read.stderr
        assert seen[-1] == '/query'
        markers = list((tmp_path / 'state/shantytown/quipu-auth').glob('*.disabled'))
        assert len(markers) == 4
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in markers)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_redaction_covers_all_shadowed_sources(tmp_path, monkeypatch):
    from shantytown.stats import _known_quipu_bearers
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('QUIPU_AUTH_TOKEN', 'inline-secret-fixture-value')
    values = []
    for relative, value in [('.config/quipu/token', 'canonical-secret-fixture-value'),
                            ('.config/aegis/quipu_token', 'legacy-secret-fixture-value'),
                            ('explicit', 'explicit-secret-fixture-value')]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        values.append(value.encode())
    monkeypatch.setenv('QUIPU_AUTH_TOKEN_FILE', str(tmp_path / 'explicit'))
    assert set(_known_quipu_bearers()) >= set(values + [b'inline-secret-fixture-value'])
