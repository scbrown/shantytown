"""Fresh hosts collect stats without an MCP kit; upgrades never double count."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from shantytown import cli, provision, runtime
from shantytown.protocols import Agent

EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop")


def captures(cfg, event):
    return [h["command"] for g in cfg.get("hooks", {}).get(event, [])
            for h in g["hooks"] if "shantytown.stats capture" in h.get("command", "")]


@pytest.mark.parametrize("role", ["worker", "lead", "administrator"])
@pytest.mark.parametrize("kit", ["absent", "empty", "mcp_only", "consent"])
def test_first_launch_and_repeat_capture_once(tmp_path, monkeypatch, role, kit):
    root = tmp_path / "store with spaces"
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(runtime, "_hook_interpreter", lambda: sys.executable)
    [settings] = cli._emit_role_settings(root, {role}, harness_name="claude")
    if kit != "absent":
        d = root / "provision"
        d.mkdir()
        if kit != "empty":
            (d / provision.MCP_TEMPLATE).write_text('{"mcpServers": {}}')
        if kit == "consent":
            (d / provision.CONSENT_TEMPLATE).write_text('{"permissions":{"deny":["WebSearch"]}}')
    card = Agent(name="fresh", role=role, workspace=str(ws), harness="claude")
    emitted = json.loads(settings.read_text())
    for _ in range(2):
        provision.provision(card, root, settings_path=settings)
        local = json.loads((ws / ".claude" / provision.CONSENT_TEMPLATE).read_text())
        for event in EVENTS:
            assert len(captures(emitted, event) + captures(local, event)) == 1
    # Execute the actual emitted command from an unrelated cwd, with the root
    # containing spaces. Neither an absent hook nor a misquoted root can pass.
    env = {**os.environ, "SHANTY_AGENT": "fresh", "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    for event in ("PostToolUse", "Stop"):
        payload = {"hook_event_name": event, "session_id": "fresh-session"}
        if event == "PostToolUse":
            payload.update(tool_name="Bash", tool_input={"command": "pwd"})
        result = subprocess.run(captures(emitted, event)[0], shell=True, cwd=ws,
                                env=env, input=json.dumps(payload), text=True,
                                capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr
    with sqlite3.connect(root / "stats.sqlite") as conn:
        assert conn.execute("SELECT agent,kind FROM events ORDER BY ts").fetchall() == [
            ("fresh", "tool"), ("fresh", "stop")]


def test_legacy_override_and_mixed_operator_hooks_survive(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_hook_interpreter", lambda: sys.executable)
    root = tmp_path / "store"
    ws = tmp_path / "workspace"
    ws.mkdir()
    [role] = cli._emit_role_settings(root, {"worker"}, harness_name="claude")
    override = root / "settings" / "agent-fresh.settings.json"
    override.write_text('{"hooks":{}}')
    path = ws / ".claude" / provision.CONSENT_TEMPLATE
    path.parent.mkdir()
    old = {"hooks": {e: [{"hooks": [{"command": "operator-hook"},
                                    {"command": "old-python -m shantytown.stats capture"}]}]
                     for e in EVENTS}, "permissions": {"deny": ["WebSearch"]}}
    path.write_text(json.dumps(old))
    card = Agent(name="fresh", workspace=str(ws), harness="claude")
    # A new role does not suppress fallback for a selected old agent override.
    for selected in (override, override, role, role):
        provision.provision(card, root, settings_path=selected)
        cfg = json.loads(path.read_text())
        for event in EVENTS:
            assert len(captures(cfg, event)) == (1 if selected == override else 0)
            assert sum(h.get("command") == "operator-hook"
                       for g in cfg["hooks"][event] for h in g["hooks"]) == 1
        assert cfg["permissions"] == old["permissions"]
