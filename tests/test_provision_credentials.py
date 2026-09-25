"""Credential isolation is proved in rendered kits, not only in the parser."""

import json
import os
import tomllib

import pytest

from shantytown import provision as P
from shantytown.protocols import Agent


def private_file(root, name, content):
    path = root / "provision" / "agents" / name / "secrets.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o600)
    return path


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_two_agents_receive_private_tokens_without_environment_export(tmp_path, monkeypatch, harness):
    monkeypatch.setenv("EXAMPLE_TOKEN", "shared-environment")
    kit = tmp_path / "provision"
    kit.mkdir()
    (kit / P.MCP_TEMPLATE).write_text(json.dumps({"mcpServers": {
        "example": {"type": "http", "url": "https://example.invalid/mcp",
                    "headers": {"Authorization": "Bearer ${EXAMPLE_TOKEN}"}},
    }}))
    private_file(tmp_path, "alice", "EXAMPLE_TOKEN=alice-private\n")
    private_file(tmp_path, "bob", "EXAMPLE_TOKEN=bob-private\n")
    for name, expected in [("alice", "alice-private"), ("bob", "bob-private"),
                           ("carol", "shared-environment")]:
        ws = tmp_path / name
        ws.mkdir()
        config = tmp_path / "settings" / "codex" / f"agent-{name}" / "config.toml"
        if harness == "codex":
            config.parent.mkdir(parents=True)
            config.write_text('model = "test-model"\n')
        P.provision(Agent(name=name, workspace=str(ws), harness=harness), tmp_path)
        target = ws / ".mcp.json"
        assert json.loads(target.read_text())["mcpServers"]["example"]["headers"]["Authorization"] == f"Bearer {expected}"
        assert target.stat().st_mode & 0o077 == 0
        if harness == "codex":
            got = tomllib.loads(config.read_text())["mcp_servers"]["example"]
            assert got["http_headers"]["Authorization"] == f"Bearer {expected}"
            assert "bearer_token_env_var" not in got
            assert config.stat().st_mode & 0o077 == 0
    assert os.environ["EXAMPLE_TOKEN"] == "shared-environment"


@pytest.mark.parametrize("bad", ["EXAMPLE_TOKEN=secret\nEXAMPLE_TOKEN=other\n",
                                 "EXAMPLE_TOKEN=\n", "bad entry secret\n"])
def test_invalid_override_refuses_without_secret_diagnostics(tmp_path, bad):
    private_file(tmp_path, "alice", bad)
    with pytest.raises(P.ProvisionError) as exc:
        P.load_secrets(tmp_path, agent="alice")
    assert "secret" not in str(exc.value)


def test_public_or_symlink_override_refuses(tmp_path):
    path = private_file(tmp_path, "alice", "EXAMPLE_TOKEN=private\n")
    path.chmod(0o644)
    with pytest.raises(P.ProvisionError, match="private"):
        P.load_secrets(tmp_path, agent="alice")
    path.chmod(0o600)
    other = path.with_name("saved")
    path.rename(other)
    path.symlink_to(other)
    with pytest.raises(P.ProvisionError, match="unreadable"):
        P.load_secrets(tmp_path, agent="alice")


def test_agent_path_cannot_escape(tmp_path):
    with pytest.raises(P.ProvisionError, match="agent name"):
        P.load_secrets(tmp_path, agent="../../outside")


def test_named_codex_cannot_overwrite_shared_role_config(tmp_path):
    private_file(tmp_path, "alice", "EXAMPLE_TOKEN=private\n")
    ws = tmp_path / "ws"
    ws.mkdir()
    role = tmp_path / "settings" / "codex" / "worker" / "config.toml"
    role.parent.mkdir(parents=True)
    role.write_text('model = "shared"\n')
    card = Agent(name="alice", workspace=str(ws), harness="codex", role="worker")
    for linked in (False, True):
        if linked:
            config = role.parent.parent / "agent-alice" / "config.toml"
            config.parent.mkdir()
            config.symlink_to(role)
        with pytest.raises(P.ProvisionError, match="independent per-agent"):
            P.provision(card, tmp_path)
        assert role.read_text() == 'model = "shared"\n'
        assert not (ws / ".mcp.json").exists()
