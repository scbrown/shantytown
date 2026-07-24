"""WHERE the untracked-work nudge is delivered from (aegis-fv2zc).

This is not a plumbing detail. The hook is delivered by PROVISION — the consent
settings the launcher re-applies on EVERY start — and not by `role set`'s
--settings, and getting that wrong is the difference between a governance hook
that runs and one that is merely committed.

The precedent is measured, in this repo, on this fleet: 693024d wired the
metrics-capture hook into --settings and it never collected fleet-wide, because
--settings is written once at `role set` and running agents never regenerate it
(aegis-rcyd). Re-confirmed 2026-07-24 before this change: the capture hook
delivered via provision is live in all 8 agents' workspaces and collecting right
now, while their --settings files predate it.

So these pin the home, the admin exemption, and — just as hard — that there is
only ONE home. Claude Code merges hooks from every settings source it reads.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shantytown import provision as P
from shantytown.protocols import Agent
from shantytown.runtime import settings_for_role


TEMPLATE = {"mcpServers": {"bobbin": {"type": "http",
                                      "url": "http://bobbin-mcp.invalid/mcp"}}}


@pytest.fixture
def root(tmp_path):
    d = tmp_path / "provision"
    d.mkdir()
    (d / P.MCP_TEMPLATE).write_text(json.dumps(TEMPLATE))
    (d / P.CONSENT_TEMPLATE).write_text(json.dumps({"enabledMcpjsonServers": ["bobbin"]}))
    return tmp_path


def _consent(root, ws) -> dict:
    return json.loads((Path(ws) / ".claude" / P.CONSENT_TEMPLATE).read_text())


def _untracked_entries(cfg: dict) -> list:
    return [e for e in cfg.get("hooks", {}).get("PreToolUse", [])
            if any("shantytown.untracked" in h.get("command", "")
                   for h in e.get("hooks", []))]


def _provision(root, tmp_path, role: str, name="a") -> dict:
    ws = tmp_path / f"ws-{name}"
    ws.mkdir()
    P.provision(Agent(name=name, role=role, reports_to=None, pane="p",
                      workspace=str(ws)), root)
    return _consent(root, ws)


# --- the home ----------------------------------------------------------------

def test_a_provisioned_worker_gets_the_nudge(root, tmp_path):
    """The one that says the feature is not dark."""
    assert _untracked_entries(_provision(root, tmp_path, "worker")) != []


def test_a_provisioned_lead_gets_the_nudge(root, tmp_path):
    """A lead is a worker that also absorbs (tier.py) — it does bead-tracked work
    too, so it is governed like one."""
    assert _untracked_entries(_provision(root, tmp_path, "lead", "l")) != []


def test_a_provisioned_administrator_does_NOT(root, tmp_path):
    """The directive exempts coordinators explicitly: acting with an empty hook
    while dispatching, triaging and draining IS the job."""
    assert _untracked_entries(_provision(root, tmp_path, "administrator", "b")) == []


def test_the_nudge_is_wired_in_exactly_ONE_place(root, tmp_path):
    """Claude Code merges hooks from every settings source. Two homes = two
    firings per tool call = double strikes and an escalation at half the
    threshold — a governance hook that is wrong in the direction of louder."""
    in_provision = len(_untracked_entries(_provision(root, tmp_path, "worker")))
    in_settings = len([
        h for entry in settings_for_role("worker")["hooks"].get("PreToolUse", [])
        for h in entry.get("hooks", [])
        if "shantytown.untracked" in h["command"]])
    assert in_provision == 1 and in_settings == 0


# --- the properties the delivery must not lose -------------------------------

def test_the_nudge_matches_ACTING_tools_only(root, tmp_path):
    """Read/Grep/Glob are deliberately absent. Looking around is exactly what an
    agent between beads SHOULD be doing, and warning it for orienting itself
    would fire the nudge at the one moment an empty hook is correct."""
    matcher = _untracked_entries(_provision(root, tmp_path, "worker"))[0]["matcher"]
    for acting in ("Edit", "Write", "Bash"):
        assert acting in matcher, f"the nudge does not cover {acting}"
    for looking in ("Read", "Grep", "Glob"):
        assert looking not in matcher, f"fires on {looking} — orienting is not drift"


def test_the_nudge_carries_an_absolute_root(root, tmp_path):
    """Same measured reason as the Stop hooks (aegis-nipg): the agent runs in ITS
    OWN workspace, which has no .shanty, so an unrooted hook resolves a registry
    that is not there and decides nothing — wired, running, and inert."""
    cmd = _untracked_entries(_provision(root, tmp_path, "worker"))[0]["hooks"][0]["command"]
    assert f"--root {Path(root).resolve()}" in cmd


def test_reprovisioning_does_not_stack_the_hook(root, tmp_path):
    """provision is idempotent and the launcher calls it on EVERY start. An
    injector that appended blindly would add one entry per launch, and a
    long-lived agent would eventually fire the nudge a dozen times per tool
    call."""
    ws = tmp_path / "ws-r"
    ws.mkdir()
    card = Agent(name="r", role="worker", reports_to=None, pane="p",
                 workspace=str(ws))
    for _ in range(3):
        P.provision(card, root)
    assert len(_untracked_entries(_consent(root, ws))) == 1


def test_the_capture_hook_survives_the_injection(root, tmp_path):
    """Two injectors write the same file. The second must not eat the first —
    that would trade a governance gap for a metrics gap."""
    cfg = _provision(root, tmp_path, "worker")
    post = [h["command"] for e in cfg["hooks"].get("PostToolUse", [])
            for h in e.get("hooks", [])]
    assert [c for c in post if "shantytown.stats" in c]


def test_a_non_json_consent_template_passes_through(tmp_path):
    """This helper must never be the reason provisioning fails — the same rule
    _with_capture_hook and _consent_for_role already follow."""
    assert P._with_untracked_hook("not json at all", "worker", tmp_path) == "not json at all"
    assert P._with_untracked_hook("[1,2,3]", "worker", tmp_path) == "[1,2,3]"


def test_template_pre_tool_use_entries_are_kept(tmp_path):
    """APPEND, not assign. A deployment template that ships its own PreToolUse
    guard keeps it."""
    text = json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "/deployment/guard.sh"}]}]}})
    cfg = json.loads(P._with_untracked_hook(text, "worker", tmp_path))
    cmds = [h["command"] for e in cfg["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert "/deployment/guard.sh" in cmds
    assert [c for c in cmds if "shantytown.untracked" in c]
