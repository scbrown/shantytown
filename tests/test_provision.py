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
def root(tmp_path, monkeypatch):
    monkeypatch.delenv("HOMELAB_MCP_TOKEN", raising=False)
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


# --- the metrics-capture hook lands on EVERY provisioned agent (aegis-rcyd) --

def test_capture_hook_injected_with_real_interpreter_and_root(root, ws):
    """Every provisioned agent must get the PostToolUse capture hook so st stats
    collects mcp__*/Skill/CLI from launch — baked with an interpreter that can
    import shantytown (never a bare 'python') and THIS store's root."""
    P.provision(_card(ws), root)
    d = json.loads((ws / ".claude" / P.CONSENT_TEMPLATE).read_text())
    post = d["hooks"]["PostToolUse"]
    assert len(post) == 1 and post[0]["matcher"] == ".*"
    cmd = post[0]["hooks"][0]["command"]
    assert "shantytown.stats capture" in cmd
    assert f"--root {Path(root).resolve()}" in cmd
    assert not cmd.startswith("python "), "bare 'python' is not on PATH (tim)"
    advisory = post[0]["hooks"][1]
    assert "yupana hook post-edit" in advisory["command"]
    assert advisory["timeout"] == 5
    assert "|| exit 0" in advisory["command"], "advisory must fail open"


def test_capture_injection_never_breaks_provisioning_on_a_non_json_template(root, ws):
    """The consent transform must never be why provisioning fails: a non-JSON
    consent template passes through verbatim (no hook, but no crash)."""
    (root / "provision" / P.CONSENT_TEMPLATE).write_text("not json at all")
    P.provision(_card(ws), root)  # must not raise
    assert (ws / ".claude" / P.CONSENT_TEMPLATE).read_text() == "not json at all"


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


def test_codex_projects_each_bearer_from_its_own_template_env_var():
    """Two authenticated MCPs need not share one credential.

    The rendered Claude kit contains secret VALUES, but Codex must recover the
    placeholder NAMES from the template so its config points at the right
    long-lived daemon environment without writing either value into TOML.
    """
    rendered = {
        "homelab": {"type": "http", "url": "http://homelab.invalid/mcp",
                    "headers": {"Authorization": "Bearer homelab-value"}},
        "agent": {"type": "http", "url": "http://agent.invalid/mcp",
                  "headers": {"Authorization": "Bearer agent-value"}},
    }
    template = {
        "homelab": {"headers": {
            "Authorization": "Bearer ${HOMELAB_MCP_TOKEN}"}},
        "agent": {"headers": {
            "Authorization": "Bearer ${AGENT_MCP_TOKEN}"}},
    }

    projected = P._codex_servers(rendered, template)

    assert projected["homelab"]["bearer_token_env_var"] == "HOMELAB_MCP_TOKEN"
    assert projected["agent"]["bearer_token_env_var"] == "AGENT_MCP_TOKEN"
    assert "http_headers" not in projected["homelab"]
    assert "http_headers" not in projected["agent"]


def test_the_rendered_file_is_not_world_readable(root, ws):
    P.provision(_card(ws), root)
    assert oct((ws / ".mcp.json").stat().st_mode)[-3:] == "600", \
        "a bearer token was written readable to everything on the host"


def test_the_consent_pre_answer_is_written(root, ws):
    P.provision(_card(ws), root)
    assert json.loads((ws / ".claude" / P.CONSENT_TEMPLATE).read_text())


def test_the_picker_deny_reaches_a_worker(root, ws):
    # aegis-qxc2: a worker's option-picker blocks its pane invisibly. The deny
    # in the consent template must land verbatim in a WORKER's workspace.
    d = root / "provision"
    (d / P.CONSENT_TEMPLATE).write_text(json.dumps(
        {"permissions": {"deny": ["AskUserQuestion"]},
         "enabledMcpjsonServers": ["bobbin"]}))
    P.provision(Agent(name="ellie", role="worker", workspace=str(ws)), root)
    cfg = json.loads((ws / ".claude" / P.CONSENT_TEMPLATE).read_text())
    assert cfg["permissions"]["deny"] == ["AskUserQuestion"]


def test_the_picker_deny_does_NOT_reach_a_lead_or_administrator(root, ws):
    # The administrator's picker is a HUMAN channel (answered by the overseer
    # over remote control). Role-blind rendering would sever the human, not the
    # stall — only AskUserQuestion is stripped, the rest of the deny survives.
    d = root / "provision"
    (d / P.CONSENT_TEMPLATE).write_text(json.dumps(
        {"permissions": {"deny": ["AskUserQuestion", "WebSearch"]},
         "enabledMcpjsonServers": ["bobbin"]}))
    for role in ("lead", "administrator"):
        P.provision(Agent(name="dearing", role=role, workspace=str(ws)), root)
        cfg = json.loads((ws / ".claude" / P.CONSENT_TEMPLATE).read_text())
        assert cfg["permissions"]["deny"] == ["WebSearch"], role
        assert cfg["enabledMcpjsonServers"] == ["bobbin"], \
            "the strip must not disturb the rest of the consent file"


def test_an_empty_deny_is_removed_not_left_as_debris(root, ws):
    # A lead whose only deny was the picker gets NO permissions key at all —
    # an empty {"permissions": {"deny": []}} reads as intent nobody has.
    d = root / "provision"
    (d / P.CONSENT_TEMPLATE).write_text(json.dumps(
        {"permissions": {"deny": ["AskUserQuestion"]},
         "enabledMcpjsonServers": ["bobbin"]}))
    P.provision(Agent(name="sattler", role="administrator", workspace=str(ws)), root)
    cfg = json.loads((ws / ".claude" / P.CONSENT_TEMPLATE).read_text())
    assert "permissions" not in cfg


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


# --- SKILLS ARE KIT: the runtime loads .claude/skills/, git ships skills/ ----
#
# Measured on the aegis deployment 2026-07-24 (aegis-atm3 / aegis-qvxd): 8 of 23
# crew clones had NO .claude/skills directory at all — loading ZERO skills — and
# 6 more were partial, because the links were made by hand once and nothing
# maintained them. Then a new skill landed correctly in git and reached 1 of 24
# runtimes. These tests pin the by-construction fix: provision links, every
# launch, and a link is never a copy.

def _skill(ws, name, body="---\nname: x\n---\nbody\n"):
    d = ws / P.SKILLS_SRC / name; d.mkdir(parents=True)
    (d / "SKILL.md").write_text(body)
    return d


def test_provision_links_every_skill_into_the_runtime_dir(root, ws):
    _skill(ws, "bundle"); _skill(ws, "quipu")
    (ws / P.SKILLS_SRC / "not-a-skill").mkdir()        # no SKILL.md: not a skill

    P.provision(_card(ws), root)

    dst = ws.joinpath(*P.SKILLS_RUNTIME)
    assert sorted(p.name for p in dst.iterdir()) == ["bundle", "quipu"]
    for n in ("bundle", "quipu"):
        assert (dst / n).is_symlink(), f"{n} must be a LINK — a copy tracks no fix"
        assert (dst / n / "SKILL.md").is_file(), "link must resolve to a real skill"
    assert P.skills_linked(ws) == ["bundle", "quipu"]
    assert P.codex_skills_linked(ws) == ["bundle", "quipu"]


def test_a_stale_COPY_is_replaced_by_a_link(root, ws):
    """The silent-drift generator: byte-identical the day it was made, tracking
    nothing after. A stale graph-report copy shadowed the source for ~24 days."""
    _skill(ws, "graph-report", "---\nname: graph-report\n---\nCURRENT\n")
    stale = ws.joinpath(*P.SKILLS_RUNTIME) / "graph-report"; stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("---\nname: graph-report\n---\nSTALE\n")

    P.provision(_card(ws), root)

    assert stale.is_symlink()
    assert "CURRENT" in (stale / "SKILL.md").read_text()


def test_a_correct_link_is_left_alone_and_a_wrong_one_is_repaired(root, ws):
    _skill(ws, "bundle")
    dst = ws.joinpath(*P.SKILLS_RUNTIME); dst.mkdir(parents=True)
    (dst / "bundle").symlink_to(ws / P.SKILLS_SRC / "bundle")
    before = os.lstat(dst / "bundle").st_ino
    (dst / "dangling").symlink_to(ws / P.SKILLS_SRC / "gone")   # no source twin
    _skill(ws, "quipu")
    (dst / "quipu").symlink_to(ws / "elsewhere")                # points nowhere

    P.provision(_card(ws), root)          # IDEMPOTENT + ADDITIVE: safe on a live agent

    assert os.lstat(dst / "bundle").st_ino == before, "rewrote an already-correct link"
    assert os.readlink(dst / "quipu") == str(ws / P.SKILLS_SRC / "quipu")
    assert (dst / "dangling").is_symlink(), "touched a name with no source twin"


def test_a_workspace_with_no_skills_is_not_an_error(root, ws):
    P.provision(_card(ws), root)
    assert P.link_skills(ws) == [] and P.skills_linked(ws) == []
    assert not ws.joinpath(*P.SKILLS_RUNTIME).exists(), "made an empty runtime dir"


def test_skills_link_even_when_the_store_defines_NO_mcp_template(tmp_path, ws):
    """A store with no MCP kit still has a workspace full of unreachable skills."""
    _skill(ws, "bundle")
    assert P.provision(_card(ws), tmp_path) == []      # no template: no servers
    assert P.skills_linked(ws) == ["bundle"], "skills rode on the MCP early-return"


def test_missing_kit_NAMES_the_unlinked_skills(root, ws):
    """The 'wire the detector to something that runs' half (aegis-qvxd): tend's
    supervision pass already calls this, and it runs THIS code — so the detector
    can never be older than the fleet it judges (a hand-run guard false-failed
    off its own 124-commit-stale checkout)."""
    _skill(ws, "bundle"); _skill(ws, "quipu")
    P.provision(_card(ws), root)
    assert P.missing_kit(_card(ws), root) == []

    (ws.joinpath(*P.SKILLS_RUNTIME) / "quipu").unlink()        # drift, post-launch
    gaps = P.missing_kit(_card(ws), root)
    assert gaps == ["skills(quipu)"], gaps
