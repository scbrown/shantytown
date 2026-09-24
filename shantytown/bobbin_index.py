"""bobbin_index — does each agent's bobbin MCP actually serve an index? (aegis-m8beqp)

A provisioned bobbin entry is not a working one. Measured 2026-09-23: an agent's
bobbin MCP was registered, connected, and answered every search with nothing,
because it was a local `bobbin serve` over an index of 0 files, while the fleet
server held 44,644. Presence checks passed; the agent had no code search at all
and nothing said so. So this asks the server the question an agent depends on:
how many files do you serve?

Three verdicts, never two:

  OK           the server answered and serves > 0 files
  EMPTY        the server answered and serves 0 files (or is not initialized)
               -- a real fault, exit 1
  CANNOT_TELL  we could not ask (unreachable, unparseable, unknown entry shape)
               -- exit 2, and never rounded to either of the others

Entries are probed once per distinct target: every crew typically shares one
HTTP server, and asking it forty times is noise, not coverage.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

OK, EMPTY, CANNOT_TELL = "ok", "empty", "cannot tell"
TIMEOUT_S = 20


@dataclass(frozen=True)
class Row:
    target: str
    agents: tuple[str, ...]
    verdict: str
    files: int | None
    why: str


def bobbin_entry(workspace: Path) -> dict | None:
    """The `bobbin` server from a workspace's provisioned .mcp.json, if any."""
    try:
        data = json.loads((Path(workspace) / ".mcp.json").read_text())
    except (OSError, ValueError):
        return None
    servers = data.get("mcpServers", data) if isinstance(data, dict) else {}
    entry = servers.get("bobbin") if isinstance(servers, dict) else None
    return entry if isinstance(entry, dict) else None


def target_of(entry: dict, workspace: Path) -> str:
    if entry.get("url"):
        return entry["url"]
    # A local server's index is per workspace, so the workspace IS the target.
    return f"local:{Path(workspace)}"


def files_from_status(status: dict) -> int | None:
    """total_files from a bobbin status payload; 0 for an uninitialized index."""
    if status.get("status") == "not_initialized":
        return 0
    value = status.get("total_files")
    if value is None and isinstance(status.get("index"), dict):
        # A remote-mode bobbin MCP proxies the server's HTTP /status verbatim,
        # where the count is nested under `index`.
        value = status["index"].get("total_files")
    return value if isinstance(value, int) else None


def _sse_json(body: str) -> dict:
    """An MCP streamable-HTTP reply is either JSON or SSE `data:` frames."""
    body = body.strip()
    if body.startswith("{"):
        return json.loads(body)
    for line in body.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise ValueError("no JSON-RPC message in reply")


def _post(url: str, payload: dict, session: str | None, *, opener) -> tuple[str, str | None]:
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers,
                                 method="POST")
    with opener(req, timeout=TIMEOUT_S) as resp:
        return resp.read().decode("utf-8", "replace"), resp.headers.get("Mcp-Session-Id")


def probe_http(url: str, headers_env: dict | None = None, *, opener=urllib.request.urlopen) -> dict:
    """initialize -> notifications/initialized -> tools/call status."""
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "st-doctor", "version": "1"}}}
    body, session = _post(url, init, None, opener=opener)
    _sse_json(body)  # must be a JSON-RPC reply, or we are not talking to MCP
    _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session, opener=opener)
    body, _ = _post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": "status", "arguments": {}}}, session, opener=opener)
    reply = _sse_json(body)
    if "error" in reply:
        raise ValueError(f"status tool error: {reply['error']}")
    text = reply["result"]["content"][0]["text"]
    return json.loads(text)


def probe_local(entry: dict, workspace: Path, *, run=subprocess.run) -> dict:
    """Ask the same binary the entry launches, in the workspace it serves."""
    command = entry.get("command") or "bobbin"
    env = {**os.environ, **{k: str(v) for k, v in (entry.get("env") or {}).items()}}
    r = run([command, "status", "--json"], cwd=workspace, capture_output=True, text=True,
            timeout=TIMEOUT_S, env=env, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"`{command} status --json` exit {r.returncode}: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout)


def survey(cards, *, workspace_of, probe_http=probe_http, probe_local=probe_local) -> list[Row]:
    targets: dict[str, tuple[dict, Path, list[str]]] = {}
    for card in cards:
        ws = workspace_of(card)
        if ws is None:
            continue
        entry = bobbin_entry(ws)
        if entry is None:
            continue
        t = target_of(entry, ws)
        targets.setdefault(t, (entry, Path(ws), []))[2].append(card.name)
    rows = []
    for t, (entry, ws, agents) in sorted(targets.items()):
        try:
            status = probe_http(entry["url"]) if entry.get("url") else probe_local(entry, ws)
            files = files_from_status(status)
        except Exception as e:  # noqa: BLE001 -- recorded as CANNOT_TELL, never as a pass
            rows.append(Row(t, tuple(sorted(agents)), CANNOT_TELL, None, f"{type(e).__name__}: {e}"[:200]))
            continue
        if files is None:
            rows.append(Row(t, tuple(sorted(agents)), CANNOT_TELL, None, "status carried no file count"))
        elif files == 0:
            rows.append(Row(t, tuple(sorted(agents)), EMPTY, 0,
                            "serves an EMPTY index: every search these agents run returns nothing"))
        else:
            rows.append(Row(t, tuple(sorted(agents)), OK, files, f"{files:,} files"))
    return rows


def worst_exit(rows: list[Row]) -> int:
    verdicts = {r.verdict for r in rows}
    if CANNOT_TELL in verdicts:
        return 2
    return 1 if EMPTY in verdicts else 0


def render(rows: list[Row]) -> str:
    out = ["  BOBBIN INDEX (what each agent's bobbin MCP actually serves)"]
    if not rows:
        out.append("  - no provisioned bobbin MCP entry found in any agent workspace")
    for r in rows:
        mark = {OK: "✓", EMPTY: "✗", CANNOT_TELL: "?"}[r.verdict]
        who = ", ".join(r.agents[:6]) + (f" +{len(r.agents) - 6}" if len(r.agents) > 6 else "")
        out.append(f"  {mark} {r.target}: {r.why}  [{who}]")
    return "\n".join(out)
