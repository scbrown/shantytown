"""Read declarations cross real HTTP/subprocess boundaries without env leaks."""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from shantytown import seeds_plate
from shantytown.query_identity import declared_query_identity, declared_read_operation, read_headers, sdk_subprocess_options


@pytest.fixture
def clean_context(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("QUIPU_", "SEEDS_", "SHANTY_", "CODEX_", "CLAUDE")):
            monkeypatch.delenv(key)


@pytest.fixture
def transport():
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((dict(self.headers), body))
            response = json.dumps({"result": False}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(response)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def query(url):
    return seeds_plate._query(SimpleNamespace(quipu_url=url), "ASK {}", 5)


def lower(headers):
    return {k.lower(): v for k, v in headers.items()}


def test_real_http_explicit_context_wins_and_preserves_task(clean_context, monkeypatch, transport):
    url, seen = transport
    for key, value in {"QUIPU_AGENT": "declared-reader", "QUIPU_TASK": "test-work",
                       "QUIPU_SESSION": "declared-session", "SEEDS_TASK": "old-work",
                       "SEEDS_SESSION": "old-session"}.items():
        monkeypatch.setenv(key, value)
    before = dict(os.environ)
    with declared_query_identity("nested-reader", source="st-tend", operation="tend"):
        assert query(url) == {"result": False}
    h = lower(seen[0][0])
    assert h["x-quipu-agent"] == "declared-reader"
    assert h["x-quipu-task"] == "test-work"
    assert h["x-quipu-session"] == "declared-session"
    assert h["x-quipu-source"] == "st-tend"
    assert h["x-quipu-operation"] == "tend"
    assert h["x-quipu-client"] == "seeds"
    assert len(seen) == 1 and seen[0][1] == {"query": "ASK {}"}
    assert dict(os.environ) == before


def test_missing_caller_and_stale_inherited_identity_are_not_crew(clean_context, monkeypatch, transport):
    url, seen = transport
    monkeypatch.setenv("SHANTY_AGENT", "stale-crew")
    with declared_query_identity(None, source="st-tend", operation="tend"):
        query(url)
    h = lower(seen[0][0])
    assert "x-quipu-agent" not in h and "x-quipu-task" not in h
    assert h["x-quipu-source"] == "st-tend"
    monkeypatch.setenv("CODEX_HOME", "declared-harness")
    query(url)
    assert lower(seen[1][0])["x-quipu-agent"] == "stale-crew"


def test_real_child_receives_known_metadata_parent_and_write_actor_unchanged(clean_context, monkeypatch):
    monkeypatch.setenv("SEEDS_TASK", "test-work")
    monkeypatch.setenv("SEEDS_SESSION", "declared-session")
    monkeypatch.setenv("SEEDS_ACTOR", "writer-control")
    before = dict(os.environ)
    code = "import json,os;print(json.dumps({k:v for k,v in os.environ.items() if k.startswith(('QUIPU_', 'SEEDS_'))}))"
    with declared_query_identity("declared-reader", source="st-tend"):
        child = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                               check=True, **sdk_subprocess_options(read_operation="ready"))
    values = json.loads(child.stdout)
    assert values["QUIPU_AGENT"] == "declared-reader"
    assert values["QUIPU_TASK"] == "test-work"
    assert values["QUIPU_SESSION"] == "declared-session"
    assert values["QUIPU_SOURCE"] == "st-tend"
    assert values["QUIPU_OPERATION"] == "ready"
    assert values["SEEDS_ACTOR"] == "writer-control"
    assert dict(os.environ) == before


def test_invalid_task_not_reassigned_and_nested_context_restored(clean_context, monkeypatch):
    monkeypatch.setenv("QUIPU_TASK", "bad task")
    monkeypatch.setenv("SEEDS_TASK", "other-task")
    with declared_query_identity(None, source="st-tend", operation="tend"):
        assert "X-Quipu-Task" not in read_headers()
        with pytest.raises(ValueError), declared_query_identity("nested", source="child", operation="ready"):
            raise ValueError("control")
        assert read_headers()["X-Quipu-Source"] == "st-tend"
        # Legacy write provenance stays identical to the pre-cut tend contract.
        assert sdk_subprocess_options()["env"]["QUIPU_AGENT"] == "st-tend"
    assert read_headers()["X-Quipu-Source"] == "shantytown"


def test_operation_scope_restores_caller_source_and_parent_operation(clean_context):
    with declared_query_identity("declared-reader", source="st-tend", operation="tend"):
        with declared_read_operation("ready-count"):
            headers = read_headers()
            assert headers["X-Quipu-Agent"] == "declared-reader"
            assert headers["X-Quipu-Source"] == "st-tend"
            assert headers["X-Quipu-Operation"] == "ready-count"
        assert read_headers()["X-Quipu-Operation"] == "tend"


def test_malformed_explicit_caller_stays_unknown_not_a_different_reader(clean_context, monkeypatch):
    monkeypatch.setenv("QUIPU_AGENT", "bad\nreader")
    monkeypatch.setenv("SEEDS_AGENT_NAME", "other-reader")
    with declared_query_identity("nested-reader"):
        assert "X-Quipu-Agent" not in read_headers()


@pytest.mark.parametrize("declared,local,expected", [
    (None, "local-host", "local-host"),
    ("declared-host", "local-host", "declared-host"),
    ("EXPLICIT.EXAMPLE", "local-host", "explicit.example"),
    ("bad\nhost", "local-host", None),
    ("", "local-host", None),
    (None, "bad host", None),
])
def test_host_header_crosses_http_without_inventing_caller(
        clean_context, monkeypatch, transport, declared, local, expected):
    from shantytown import query_identity
    monkeypatch.setattr(query_identity.socket, "gethostname", lambda: local)
    if declared is not None:
        monkeypatch.setenv("QUIPU_HOST", declared)
    before = dict(os.environ)
    url, seen = transport
    assert query(url) == {"result": False}
    headers = lower(seen[0][0])
    assert headers.get("x-quipu-host") == expected
    assert headers["x-quipu-client"] == "seeds"
    assert "x-quipu-agent" not in headers
    assert seen[0][1] == {"query": "ASK {}"}
    assert dict(os.environ) == before


def test_hostname_failure_stays_unknown(clean_context, monkeypatch, transport):
    from shantytown import query_identity
    def unavailable():
        raise OSError("no local hostname")
    monkeypatch.setattr(query_identity.socket, "gethostname", unavailable)
    url, seen = transport
    query(url)
    assert "x-quipu-host" not in lower(seen[0][0])
    monkeypatch.setenv("QUIPU_HOST", "declared-host")
    query(url)
    assert lower(seen[1][0])["x-quipu-host"] == "declared-host"
