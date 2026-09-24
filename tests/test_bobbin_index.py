"""bobbin_index: an agent whose bobbin MCP serves 0 files must FAIL doctor (aegis-m8beqp)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from shantytown import bobbin_index as bix


@dataclass
class Card:
    name: str
    workspace: str | None
    retired: bool = False


def _ws(tmp_path: Path, name: str, entry: dict | None) -> Path:
    ws = tmp_path / name
    ws.mkdir()
    if entry is not None:
        (ws / ".mcp.json").write_text(json.dumps({"mcpServers": {"bobbin": entry}}))
    return ws


def _survey(cards, **kw):
    return bix.survey(cards, workspace_of=lambda c: Path(c.workspace) if c.workspace else None, **kw)


# The reply shape measured from a live bobbin 0.17.1 MCP server.
LIVE_SSE = ('data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":'
            '"{\\n  \\"status\\": \\"ready\\",\\n  \\"total_files\\": 44729\\n}"}]}}\n\n')


def test_sse_reply_parses_to_the_status_payload():
    reply = bix._sse_json(LIVE_SSE)
    assert bix.files_from_status(json.loads(reply["result"]["content"][0]["text"])) == 44729


def test_empty_and_uninitialized_are_both_empty():
    assert bix.files_from_status({"status": "ready", "total_files": 0}) == 0
    assert bix.files_from_status({"status": "not_initialized", "path": "/x"}) == 0
    assert bix.files_from_status({"status": "ready"}) is None


def test_remote_mode_http_status_shape_is_read():
    # Shape of a bobbin server's HTTP /status, which a remote-mode MCP passes through.
    http_status = {"status": "ok", "index": {"total_files": 44729, "total_chunks": 189406},
                   "sources": {}, "quipu_endpoint": "http://quipu.example"}
    assert bix.files_from_status(http_status) == 44729
    assert bix.files_from_status({"status": "ok", "index": {"total_files": 0}}) == 0


def test_a_zero_file_local_index_fails_while_the_shared_server_passes(tmp_path):
    shared = {"type": "http", "url": "http://bobbin.example/mcp"}
    cards = [
        Card("ada", str(_ws(tmp_path, "ada", shared))),
        Card("bo", str(_ws(tmp_path, "bo", shared))),
        Card("cy", str(_ws(tmp_path, "cy", {"command": "bobbin", "args": ["serve"]}))),
        Card("dee", str(_ws(tmp_path, "dee", None))),          # no bobbin entry: not probed
        Card("gone", None),
    ]
    http_calls = []

    def fake_http(url):
        http_calls.append(url)
        return {"status": "ready", "total_files": 44729}

    rows = _survey(cards, probe_http=fake_http,
                   probe_local=lambda entry, ws: {"status": "ready", "total_files": 0})
    by = {r.target: r for r in rows}
    assert http_calls == ["http://bobbin.example/mcp"]          # probed once, shared by two agents
    assert by["http://bobbin.example/mcp"].verdict == bix.OK
    assert by["http://bobbin.example/mcp"].agents == ("ada", "bo")
    local = next(r for r in rows if r.target.startswith("local:"))
    assert local.verdict == bix.EMPTY and local.agents == ("cy",)
    assert bix.worst_exit(rows) == 1
    assert "EMPTY index" in bix.render(rows)


def test_could_not_ask_is_cannot_tell_never_a_pass(tmp_path):
    cards = [Card("ada", str(_ws(tmp_path, "ada", {"type": "http", "url": "http://down.example/mcp"})))]

    def down(url):
        raise OSError("connection refused")

    rows = _survey(cards, probe_http=down)
    assert rows[0].verdict == bix.CANNOT_TELL
    assert bix.worst_exit(rows) == 2


def test_cannot_tell_outranks_a_fault(tmp_path):
    rows = [bix.Row("a", ("x",), bix.EMPTY, 0, ""), bix.Row("b", ("y",), bix.CANNOT_TELL, None, "")]
    assert bix.worst_exit(rows) == 2
    assert bix.worst_exit([bix.Row("a", ("x",), bix.OK, 5, "")]) == 0


def test_http_probe_does_the_mcp_handshake(tmp_path):
    seen = []

    class Resp:
        def __init__(self, body, session=None):
            self._b, self.headers = body.encode(), {"Mcp-Session-Id": session} if session else {}

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        payload = json.loads(req.data)
        seen.append((payload.get("method"), req.get_header("Mcp-session-id")))
        if payload.get("method") == "initialize":
            return Resp('data: {"jsonrpc":"2.0","id":1,"result":{}}\n', "sess-1")
        if payload.get("method") == "notifications/initialized":
            return Resp("")
        return Resp(LIVE_SSE)

    status = bix.probe_http("http://bobbin.example/mcp", opener=opener)
    assert status["total_files"] == 44729
    assert seen == [("initialize", None), ("notifications/initialized", "sess-1"),
                    ("tools/call", "sess-1")]


def test_local_probe_runs_the_entrys_binary_in_the_workspace(tmp_path):
    calls = []

    class R:
        returncode, stdout, stderr = 0, '{"status":"not_initialized","path":"/w"}', ""

    def run(argv, **kw):
        calls.append((argv, kw["cwd"]))
        return R()

    status = bix.probe_local({"command": "/opt/bobbin", "args": ["serve"]}, tmp_path, run=run)
    assert calls == [(["/opt/bobbin", "status", "--json"], tmp_path)]
    assert bix.files_from_status(status) == 0


def test_local_probe_failure_raises_so_survey_reports_cannot_tell(tmp_path):
    class R:
        returncode, stdout, stderr = 2, "", "boom"

    with pytest.raises(RuntimeError):
        bix.probe_local({"command": "bobbin"}, tmp_path, run=lambda argv, **kw: R())
