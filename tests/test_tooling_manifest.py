"""Source agreement, not agreement between two equally stale projections."""
import copy
import json
import tomllib

import pytest

from shantytown import provision as P, tooling as T
from shantytown.answer import Answer
from shantytown.protocols import Agent
from shantytown.quipu import QuipuUnreachable, QuipuQueryRejected


IRI = "urn:example:tooling"
DATA = {
    "version": 1,
    "mcpServers": {"search": {"type": "http", "url": "https://example.invalid/mcp",
                              "headers": {"Authorization": "Bearer ${SEARCH_TOKEN}"}}},
    "skills": {"search": "canonical/search"},
    "instructions": "Query the knowledge service before starting a task.",
}


@pytest.fixture
def kit(tmp_path, monkeypatch):
    root = tmp_path / "store"
    root.mkdir()
    (root / "shantytown.toml").write_text(f'[env]\nSHANTY_TOOLING_MANIFEST = "{IRI}"\n')
    (root / "provision").mkdir()
    (root / "provision" / P.CONSENT_TEMPLATE).write_text('{}')
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "canonical/search").mkdir(parents=True)
    (ws / "canonical/search/SKILL.md").write_text("# Search\n")
    (ws / "CLAUDE.md").write_text("General Claude guidance.\n")
    (ws / "AGENTS.md").write_text("General Codex guidance.\n")
    config = root / "settings/codex/agent-worker/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('model = "example"\n[mcp_servers.old]\ncommand = "old"\n')
    monkeypatch.setenv("SEARCH_TOKEN", "test-secret")
    data = copy.deepcopy(DATA)
    calls = []
    def query(self, text):
        calls.append(text)
        return Answer.complete_read([{"value": json.dumps(data)}], how="fixture")
    monkeypatch.setattr(T.QuipuRegistry, "_query_answer", query)
    return root, ws, config, data, calls


def card(ws, harness="codex"):
    return Agent(name="worker", workspace=str(ws), harness=harness)


def test_both_harnesses_project_graph_and_preserve_other_instructions(kit):
    root, ws, config, data, calls = kit
    for harness in ("claude", "codex"):
        assert P.provision(card(ws, harness), root) == ["search"]
        assert P.missing_kit(card(ws, harness), root) == []
    assert (ws / "CLAUDE.md").read_text().startswith("General Claude guidance.")
    assert (ws / "AGENTS.md").read_text().startswith("General Codex guidance.")
    for name in ("CLAUDE.md", "AGENTS.md"):
        assert (ws / name).read_text().count(T.BEGIN) == 1
        assert data["instructions"] in (ws / name).read_text()
    cfg = tomllib.loads(config.read_text())
    assert cfg["model"] == "example"
    assert set(cfg["mcp_servers"]) == {"search"}, "removed servers must be removed from Codex too"
    assert cfg["mcp_servers"]["search"]["bearer_token_env_var"] == "SEARCH_TOKEN"
    assert "test-secret" not in config.read_text()
    assert (ws / ".mcp.json").stat().st_mode & 0o777 == 0o600
    assert all(f"<{IRI}>" in q for q in calls)


def test_endpoint_and_instruction_changes_detected_without_name_changes(kit):
    root, ws, config, data, _ = kit
    P.provision(card(ws), root)
    data["mcpServers"]["search"]["url"] = "https://changed.invalid/mcp"
    data["instructions"] = "Updated canonical guidance."
    gaps = P.missing_kit(card(ws), root)
    assert "mcp(Quipu drift)" in gaps
    assert "codex-mcp(Quipu drift)" in gaps
    assert "instructions(Quipu drift)" in gaps
    P.provision(card(ws), root)
    assert P.missing_kit(card(ws), root) == []


def test_doctor_uses_one_fresh_snapshot_for_entire_report(kit):
    root, ws, _, _, calls = kit
    P.provision(card(ws), root)
    calls.clear()
    report, broken = P.uniformity_report([card(ws), card(ws, "claude")], root)
    assert not broken, report
    assert len(calls) == 1


@pytest.mark.parametrize("result", [[], [{"value": "{}"}, {"value": "{}"}],
                                    [{"value": {}}], [{"wrong": "{}"}]])
def test_missing_or_ambiguous_graph_is_not_local_fallback(kit, monkeypatch, result):
    root, ws, _, _, _ = kit
    (root / "provision" / P.MCP_TEMPLATE).write_text(json.dumps({"mcpServers": DATA["mcpServers"]}))
    before = (ws / "AGENTS.md").read_bytes()
    monkeypatch.setattr(T.QuipuRegistry, "_query_answer", lambda *a: Answer.complete_read(result, how="fixture"))
    with pytest.raises(P.ProvisionError):
        P.provision(card(ws), root)
    assert not (ws / ".mcp.json").exists()
    assert (ws / "AGENTS.md").read_bytes() == before
    assert P.missing_kit(card(ws), root) == ["tooling-source(UNKNOWN)"]


@pytest.mark.parametrize("failure", ["unreachable", "truncated", "rejected"])
def test_unknown_doctor_never_reports_uniform(kit, monkeypatch, failure):
    root, ws, _, _, _ = kit
    def query(*args):
        if failure == "unreachable":
            raise QuipuUnreachable("sensitive upstream body")
        if failure == "rejected":
            raise QuipuQueryRejected("sensitive upstream body")
        return Answer.capped([{"value": json.dumps(DATA)}], how="fixture", caveat="cap")
    monkeypatch.setattr(T.QuipuRegistry, "_query_answer", query)
    report, broken = P.uniformity_report([card(ws)], root)
    assert broken and "UNKNOWN" in report
    assert "sensitive" not in report


def test_secret_rotation_and_wrong_skill_destination_are_drift(kit, monkeypatch):
    root, ws, _, _, _ = kit
    P.provision(card(ws), root)
    monkeypatch.setenv("SEARCH_TOKEN", "rotated-secret")
    other = ws / "other"
    other.mkdir()
    (other / "SKILL.md").write_text("wrong source")
    link = ws / ".agents/skills/search"
    link.unlink()
    link.symlink_to(other)
    gaps = P.missing_kit(card(ws), root)
    assert "mcp(Quipu drift)" in gaps
    assert ".agents-skills(search)" in gaps
    assert "secret" not in str(gaps)


def test_reverse_rulebook_symlink_does_not_become_a_loop(kit):
    root, ws, _, _, _ = kit
    (ws / "CLAUDE.md").unlink()
    (ws / "CLAUDE.md").symlink_to("AGENTS.md")
    P.provision(card(ws), root)
    assert not (ws / "AGENTS.md").is_symlink()
    assert (ws / "CLAUDE.md").read_bytes() == (ws / "AGENTS.md").read_bytes()
    assert P.missing_kit(card(ws), root) == []


@pytest.mark.parametrize("obstruction", ["skill", "override", "marker", "missing-source"])
def test_refuses_obstructions_before_writing_mcp(kit, obstruction):
    root, ws, _, _, _ = kit
    if obstruction == "skill":
        (ws / ".agents/skills/search").mkdir(parents=True)
        (ws / ".agents/skills/search/SKILL.md").write_text("personal work")
    elif obstruction == "override":
        (ws / "AGENTS.override.md").write_text("shadows AGENTS")
    elif obstruction == "marker":
        (ws / "CLAUDE.md").write_text(T.BEGIN)
    else:
        (ws / "canonical/search/SKILL.md").unlink()
    with pytest.raises(P.ProvisionError):
        P.provision(card(ws), root)
    assert not (ws / ".mcp.json").exists()


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(version=True),
    lambda d: d.update(instructions=""),
    lambda d: d.update(skills={"../escape": "/tmp"}),
    lambda d: d.update(extra="typo"),
    lambda d: d["mcpServers"]["search"].update(headers={"Authorization": "Bearer literal-secret"}),
])
def test_invalid_manifest_refused_without_echoing_content(mutation):
    data = copy.deepcopy(DATA)
    mutation(data)
    with pytest.raises(T.ToolingError) as error:
        T.parse(json.dumps(data), IRI)
    assert "literal-secret" not in str(error.value)


def test_duplicate_keys_refused():
    with pytest.raises(T.ToolingError, match="duplicate"):
        T.parse('{"version":1,"version":2}', IRI)


def test_source_iri_cannot_inject_sparql(kit, monkeypatch):
    root, _, _, _, calls = kit
    (root / "shantytown.toml").write_text('[env]\nSHANTY_TOOLING_MANIFEST = "urn:x> ?s ?p ?o"\n')
    with pytest.raises(T.ToolingError, match="IRI"):
        T.load(root)
    assert calls == []


def test_removed_skill_is_detected_and_retracted_but_personal_skill_survives(kit):
    root, ws, _, data, _ = kit
    P.provision(card(ws), root)
    personal = ws / ".agents/skills/personal"
    personal.mkdir()
    (personal / "SKILL.md").write_text("personal")
    data["skills"] = {}
    assert "skills(retired in Quipu)" in P.missing_kit(card(ws), root)
    P.provision(card(ws), root)
    assert not (ws / ".agents/skills/search").exists()
    assert not (ws / ".claude/skills/search").exists()
    assert (personal / "SKILL.md").read_text() == "personal"
    assert P.missing_kit(card(ws), root) == []


def test_explicit_secret_argument_verifies_against_same_values(kit, monkeypatch):
    root, ws, _, _, _ = kit
    monkeypatch.delenv("SEARCH_TOKEN")
    P.provision(card(ws), root, secrets={"SEARCH_TOKEN": "explicit"})
    assert "explicit" in (ws / ".mcp.json").read_text()


def test_retire_final_server_and_quote_credentials(kit, monkeypatch):
    root, ws, config, data, _ = kit
    token = 'quoted"credential\\with\nnewline'
    monkeypatch.setenv("SEARCH_TOKEN", token)
    P.provision(card(ws), root)
    assert json.loads((ws / ".mcp.json").read_text())["mcpServers"]["search"]["headers"]["Authorization"] == "Bearer " + token
    assert token not in config.read_text()
    data["mcpServers"] = {}
    assert P.provision(card(ws), root) == []
    assert tomllib.loads(config.read_text()).get("mcp_servers", {}) == {}
    assert P.missing_kit(card(ws), root) == []


def test_actual_query_carries_attribution_and_rejects_truncation(tmp_path, monkeypatch):
    from io import BytesIO
    monkeypatch.setenv("SHANTY_TOOLING_MANIFEST", IRI)
    seen = []
    def response(request, timeout):
        seen.append(request)
        return BytesIO(json.dumps({"rows": [{"value": json.dumps(DATA)}], "truncated": True}).encode())
    monkeypatch.setattr("urllib.request.urlopen", response)
    with pytest.raises(T.ToolingError, match="truncated"):
        T.load(tmp_path)
    assert seen[0].get_header("X-quipu-client") == "shantytown-tooling"
    assert json.loads(seen[0].data)["verbose"] is True


def test_claude_consent_follows_manifest_changes(kit):
    root, ws, _, data, _ = kit
    (root / "provision" / P.CONSENT_TEMPLATE).write_text(json.dumps({
        "enabledMcpjsonServers": ["retired"], "permissions": {"deny": ["ExampleTool"]}}))
    P.provision(card(ws, "claude"), root)
    path = ws / ".claude" / P.CONSENT_TEMPLATE
    consent = json.loads(path.read_text())
    assert consent["enabledMcpjsonServers"] == ["search"]
    assert consent["permissions"]["deny"] == ["ExampleTool"]
    consent["enabledMcpjsonServers"] = []
    path.write_text(json.dumps(consent))
    assert "mcp-consent(Quipu drift)" in P.missing_kit(card(ws, "claude"), root)


def test_explicit_disabled_server_refuses_before_any_projection(kit):
    root, ws, _, _, _ = kit
    (root / "provision" / P.CONSENT_TEMPLATE).write_text('{"disabledMcpjsonServers":["search"]}')
    with pytest.raises(P.ProvisionError, match="disabled"):
        P.provision(card(ws, "claude"), root)
    assert not (ws / ".mcp.json").exists()
    assert T.BEGIN not in (ws / "CLAUDE.md").read_text()
