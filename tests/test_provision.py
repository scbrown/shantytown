"""provision — an agent is FULLY EQUIPPED or it is not created.

Five agents were created from clean clones and worked P1 beads for a night with
no code search, no knowledge graph and no ops tools, because the file that wires
them is uncommitted (it carries a bearer token) and a fresh clone cannot have it.
They looked live on every surface the tier has. These tests pin the refusals that
make that impossible, and the one that matters most is the HALF-render: a config
with an empty credential fails later, elsewhere, as somebody else's bug.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

import pytest

from shantytown import provision as P
from shantytown.protocols import Agent


TEMPLATE = {
    "mcpServers": {
        "bobbin": {"type": "http", "url": "http://bobbin-mcp.invalid/mcp"},
        "homelab": {"type": "http", "url": "http://homelab-mcp.invalid/mcp",
                    "headers": {"Authorization": "Bearer ${HOMELAB_MCP_TOKEN}"}},
    }
}


@pytest.fixture
def root(tmp_path):
    d = tmp_path / "provision"; d.mkdir()
    (d / P.MCP_TEMPLATE).write_text(json.dumps(TEMPLATE))
    (d / P.CONSENT_TEMPLATE).write_text(json.dumps({"enabledMcpjsonServers": ["bobbin"]}))
    (d / P.SECRETS).write_text("HOMELAB_MCP_TOKEN=s3cr3t-value\n")
    return tmp_path


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"; w.mkdir()
    return w


def _card(ws) -> Agent:
    return Agent(name="ellie", workspace=str(ws))


# --- the kit lands, and is VERIFIED by listing ------------------------------

def test_provision_returns_the_servers_it_can_prove(root, ws):
    got = P.provision(_card(ws), root)
    assert got == ["bobbin", "homelab"]
    assert P.servers_in(ws / ".mcp.json") == got, \
        "reported a kit it did not read back out of the file it wrote"


def test_the_secret_is_injected_not_left_as_a_placeholder(root, ws):
    P.provision(_card(ws), root)
    auth = json.loads((ws / ".mcp.json").read_text())["mcpServers"]["homelab"]["headers"]["Authorization"]
    assert auth == "Bearer s3cr3t-value"
    assert "${" not in (ws / ".mcp.json").read_text()


def test_the_rendered_file_is_not_world_readable(root, ws):
    P.provision(_card(ws), root)
    assert oct((ws / ".mcp.json").stat().st_mode)[-3:] == "600", \
        "a bearer token was written readable to everything on the host"


def test_the_consent_pre_answer_is_written(root, ws):
    P.provision(_card(ws), root)
    assert json.loads((ws / ".claude" / P.CONSENT_TEMPLATE).read_text())


def test_provision_is_idempotent(root, ws):
    first = P.provision(_card(ws), root)
    body = (ws / ".mcp.json").read_text()
    assert P.provision(_card(ws), root) == first
    assert (ws / ".mcp.json").read_text() == body


# --- the refusals ------------------------------------------------------------

def test_a_missing_secret_REFUSES_rather_than_half_rendering(root, ws):
    """THE test. A rendered-empty credential produces a config that parses,
    loads, and 401s on the first call — read by the operator as a flaky service,
    in the wrong place, hours later."""
    (root / "provision" / P.SECRETS).unlink()
    with pytest.raises(P.ProvisionError) as e:
        P.provision(_card(ws), root, secrets={})
    assert "HOMELAB_MCP_TOKEN" in str(e.value)
    assert not (ws / ".mcp.json").exists(), "wrote a half-rendered config anyway"


def test_an_empty_secret_is_MISSING_not_a_value(root, ws):
    with pytest.raises(P.ProvisionError):
        P.provision(_card(ws), root, secrets={"HOMELAB_MCP_TOKEN": ""})


def test_a_workspace_that_does_not_exist_REFUSES(root, tmp_path):
    with pytest.raises(P.ProvisionError) as e:
        P.provision(Agent(name="ellie", workspace=str(tmp_path / "gone")), root)
    assert "does not exist" in str(e.value)


def test_no_template_is_NO_KIT_not_a_half_kit(tmp_path, ws):
    """The line between "this fleet wants no MCP servers" and "this agent is
    missing its tools". Refusing here would break every install that is not
    ours; the caller says the absence out loud instead."""
    assert P.provision(_card(ws), tmp_path) == []
    assert not (ws / ".mcp.json").exists()


# --- the gap report: what tend uses -----------------------------------------

def test_missing_kit_names_the_servers_that_are_absent(root, ws):
    gaps = P.missing_kit(_card(ws), root)
    assert any("bobbin" in g and "homelab" in g for g in gaps)
    assert "mcp-consent" in gaps
    P.provision(_card(ws), root)
    assert P.missing_kit(_card(ws), root) == [], "still reported gaps after provisioning"


def test_a_PARTIAL_kit_is_reported_not_passed(root, ws):
    """The shape of the bug: a file exists, and it is not the kit. Existence was
    never the question."""
    (ws / ".mcp.json").write_text(json.dumps({"mcpServers": {"bobbin": {}}}))
    (ws / ".claude").mkdir(); (ws / ".claude" / P.CONSENT_TEMPLATE).write_text("{}")
    gaps = P.missing_kit(_card(ws), root)
    assert gaps and "homelab" in gaps[0]


def test_the_environment_overrides_the_secret_file(root, ws, monkeypatch):
    monkeypatch.setenv("HOMELAB_MCP_TOKEN", "from-env")
    P.provision(_card(ws), root, secrets=P.load_secrets(root))
    assert "from-env" in (ws / ".mcp.json").read_text()


# --- the launcher refuses rather than creating a half-equipped agent ---------

def test_st_new_REFUSES_when_the_kit_cannot_be_completed(tmp_path, monkeypatch, capsys):
    from shantytown import cli
    from shantytown.tmux import NullPanes

    crew = tmp_path / "crew"; crew.mkdir()
    ws = tmp_path / "ws"; ws.mkdir()
    (crew / "ellie.json").write_text(json.dumps(
        {"role": "worker", "pane": "p-ellie", "workspace": str(ws)}))
    sdir = tmp_path / "settings"; sdir.mkdir()
    (sdir / "worker.settings.json").write_text("{}")
    d = tmp_path / "provision"; d.mkdir()
    (d / P.MCP_TEMPLATE).write_text(json.dumps(TEMPLATE))     # secret ABSENT

    panes = NullPanes(live=set())
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    monkeypatch.delenv("HOMELAB_MCP_TOKEN", raising=False)

    class _A:
        root = tmp_path; agent = "ellie"; dry_run = False
        backend = None; repo = None; registry = "files"

    assert cli._cmd_new(_A()) == cli.REFUSED
    assert "HOMELAB_MCP_TOKEN" in capsys.readouterr().err
    assert panes.sent == [], "launched a half-equipped agent"
    assert not panes.exists("p-ellie"), "created a session for one"
