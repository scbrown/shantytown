"""Register real harness artifacts from a rig card, without a launch or install plan."""
import json
import tomllib

import pytest

from shantytown import cli, provision, tooling
from shantytown.answer import Answer
from shantytown.files import FilesRegistry
from shantytown.protocols import Agent


@pytest.fixture
def rig(tmp_path, monkeypatch):
    root = tmp_path / "store"
    root.mkdir()
    (root / "crew").mkdir()
    (root / "shantytown.toml").write_text(
        '[host]\nname = "local"\n[env]\nSHANTY_TOOLING_MANIFEST = "urn:test:tools"\nSHANTY_ONTO_NS = "https://example.test/ontology/"\n')
    kit = root / "provision"
    kit.mkdir()
    (kit / provision.CONSENT_TEMPLATE).write_text('{}')
    ws = tmp_path / "workspace"
    ws.mkdir()
    data = {"version": 1, "mcpServers": {
        "yupana": {"command": "yupana", "args": ["serve"]},
        "bobbin": {"command": "bobbin", "args": ["serve"]},
        "forgejo": {"type": "http", "url": "https://forgejo.example/mcp"},
        "homelab": {"type": "http", "url": "https://homelab.example/mcp",
                    "headers": {"Authorization": "Bearer ${TEST_MCP_TOKEN}"}},
    }, "skills": {}, "instructions": "Use the canonical tools."}
    monkeypatch.setenv("TEST_MCP_TOKEN", "private-fixture-value")
    monkeypatch.setattr(tooling.QuipuRegistry, "_query_answer",
                        lambda self, query: Answer.complete_read(
                            [{"value": json.dumps(data)}], how="fixture"))
    monkeypatch.setattr(cli, "_panes", lambda *a, **kw: pytest.fail("must not touch panes"))
    return root, ws, data


def run(root, *args):
    return cli.main(["--root", str(root), "ops", "provision", "--json", *args])


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_rig_card_registers_all_manifest_servers_and_preserves_settings(rig, capsys, harness):
    root, ws, data = rig
    FilesRegistry(root / "crew").set(Agent(name="ada", workspace=str(ws), harness=harness))
    config = root / "settings/codex/agent-ada/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('model = "chosen-model"\n')
    (ws / "AGENTS.md").write_text("Personal guidance.\n")
    assert run(root) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["owner"] == "shantytown"
    assert receipt["agents"][0]["servers"] == sorted(data["mcpServers"])
    assert "private-fixture-value" not in json.dumps(receipt)
    assert provision.servers_in(ws / ".mcp.json") == sorted(data["mcpServers"])
    if harness == "codex":
        projected = tomllib.loads(config.read_text())
        assert sorted(projected["mcp_servers"]) == sorted(data["mcpServers"])
        assert projected["model"] == "chosen-model"
    assert (ws / "AGENTS.md").read_text().startswith("Personal guidance.")
    before = (ws / ".mcp.json").read_bytes()
    assert run(root, "ada") == 0
    assert (ws / ".mcp.json").read_bytes() == before


def test_empty_crew_gives_actionable_remedy(rig, capsys):
    root, ws, _ = rig
    assert run(root) != 0
    assert "no crew on this rig yet" in capsys.readouterr().err
    assert not (ws / ".mcp.json").exists()


@pytest.mark.parametrize("failure", ["no-manifest", "empty-manifest", "graph", "secret"])
def test_authority_failure_never_falls_back_to_local_template(rig, monkeypatch, capsys, failure):
    root, ws, data = rig
    FilesRegistry(root / "crew").set(Agent(name="ada", workspace=str(ws)))
    (root / "provision" / provision.MCP_TEMPLATE).write_text(
        '{"mcpServers":{"stale":{"command":"stale"}}}')
    if failure == "no-manifest":
        (root / "shantytown.toml").write_text('')
    elif failure == "empty-manifest":
        data["mcpServers"] = {}
    elif failure == "graph":
        def unavailable(root):
            raise tooling.ToolingError("graph unavailable")
        monkeypatch.setattr(tooling, "load", unavailable)
    else:
        monkeypatch.delenv("TEST_MCP_TOKEN")
    assert run(root) != 0
    assert "registration refused" in capsys.readouterr().err
    assert not (ws / ".mcp.json").exists()


def test_remote_card_and_unknown_selection_cannot_be_provisioned(rig, capsys):
    root, ws, _ = rig
    registry = FilesRegistry(root / "crew")
    registry.set(Agent(name="remote", host="elsewhere", workspace=str(ws)))
    assert run(root, "remote") != 0
    assert not (ws / ".mcp.json").exists()
    registry.set(Agent(name="local", workspace=str(ws)))
    assert run(root, "absent") != 0
    assert "not in this rig" in capsys.readouterr().err
    assert not (ws / ".mcp.json").exists()


def test_missing_registry_has_setup_remedy(rig, capsys):
    root, _, _ = rig
    (root / "crew").rmdir()
    assert run(root) != 0
    assert "run st fleet init / st agent new first" in capsys.readouterr().err


def test_missing_workspace_refuses_before_any_write(rig, capsys):
    root, ws, _ = rig
    registry = FilesRegistry(root / "crew")
    registry.set(Agent(name="ada", workspace=str(ws)))
    registry.set(Agent(name="missing"))
    assert run(root) != 0
    assert "workspace missing for missing" in capsys.readouterr().err
    assert not (ws / ".mcp.json").exists()
