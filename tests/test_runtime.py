"""Runtime launch composition — the anti-handoff seam from the launcher side.
shantytown #5 launch ruling (aegis-qdal, arnold's mail gt-wisp-gp9g2g).

The invariant under test: the composed command ALWAYS carries --settings, or it
is NOT composed at all. Plus the capability gate (a lead needs a runtime that can
deliver stop events to the model), with its positive control — a gate only ever
seen refusing, or only ever seen passing, has not been shown to work.
"""
from __future__ import annotations

import pytest

from shantytown.protocols import Agent
from shantytown.runtime import (
    ClaudeRuntime, CodexRuntime, CapabilityError, SettingsError, require_capability,
    HookSpec,
)
from shantytown.tmux import NullPanes


def _ok_settings(card):            # a resolver that always materializes
    return f"/etc/shanty/{card.role}.settings.json"


def _no_settings(card):            # a resolver that cannot materialize
    return None


# --- the invariant: --settings carried, or not composed at all --------------

def test_compose_CARRIES_settings():
    rt = ClaudeRuntime(NullPanes(), _ok_settings)
    launch = rt.compose(Agent(name="ellie", role="worker"))
    assert "--settings" in launch
    assert "/etc/shanty/worker.settings.json" in launch


def test_compose_REFUSES_when_settings_cannot_be_materialized():
    """The other half of the invariant: no settings -> RAISE, never a
    settings-less fallback launch."""
    rt = ClaudeRuntime(NullPanes(), _no_settings)
    with pytest.raises(SettingsError):
        rt.compose(Agent(name="ellie", role="worker"))


def test_compose_sets_SHANTY_AGENT_for_identity():
    """SHANTY_AGENT carries identity so `st prime` resolves the right agent."""
    rt = ClaudeRuntime(NullPanes(), _ok_settings)
    launch = rt.compose(Agent(name="malcolm", role="worker"))
    assert "SHANTY_AGENT=malcolm" in launch


# --- the capability gate, BOTH outcomes (the positive control matters) ------

def test_lead_on_claude_PASSES():
    """Positive control: the gate must OPEN for a runtime that has the capability,
    or it blocks everything and proves nothing."""
    rt = ClaudeRuntime(NullPanes(), _ok_settings)
    launch = rt.compose(Agent(name="maldoon", role="lead"))
    assert "--settings" in launch          # composed, not refused


def test_lead_on_codex_REFUSES():
    """codex declares no blocking stop hooks -> a lead cannot be hosted -> refuse,
    write nothing, launch nothing (adapters.md)."""
    rt = CodexRuntime(NullPanes(), _ok_settings)
    with pytest.raises(CapabilityError, match="blocking stop hooks"):
        rt.compose(Agent(name="maldoon", role="lead"))


def test_administrator_on_codex_REFUSES():
    """An administrator also receives risen stop events (tier.route_stop), so it
    needs the same delivery capability."""
    rt = CodexRuntime(NullPanes(), _ok_settings)
    with pytest.raises(CapabilityError):
        rt.compose(Agent(name="goldblum", role="administrator"))


def test_worker_on_codex_is_FINE():
    """codex can host workers — the gate is role-specific, not a blanket ban."""
    rt = CodexRuntime(NullPanes(), _ok_settings)
    launch = rt.compose(Agent(name="ellie", role="worker"))
    assert "codex" in launch and "--settings" in launch


def test_capability_gate_keys_on_declaration_not_name():
    """require_capability trusts hooks(), not a hardcoded name — a runtime that
    DECLARES blocking_stop passes even if it isn't claude."""
    class _CapableOther:
        name = "opencode"
        def hooks(self, card): return HookSpec(blocking_stop=True)
    require_capability(_CapableOther(), Agent(name="x", role="lead"))   # must not raise


# --- start() is the seam: compose THEN deliver through Panes ----------------

def test_start_sends_the_composed_launch_through_panes():
    panes = NullPanes()
    rt = ClaudeRuntime(panes, _ok_settings)
    rt.start(Agent(name="ellie", role="worker"), "aegis-crew-ellie")
    assert len(panes.sent) == 1
    pane, text = panes.sent[0]
    assert pane == "aegis-crew-ellie"
    assert "SHANTY_AGENT=ellie" in text and "--settings" in text


def test_start_REFUSES_before_sending_when_settings_missing():
    """A refusal must launch NOTHING — no send may happen."""
    panes = NullPanes()
    rt = ClaudeRuntime(panes, _no_settings)
    with pytest.raises(SettingsError):
        rt.start(Agent(name="ellie", role="worker"), "aegis-crew-ellie")
    assert panes.sent == [], "refused compose must not have sent anything"


# --- is_live: the process-verify predicate, both outcomes -------------------

def test_is_live_true_on_the_ready_banner():
    rt = ClaudeRuntime(NullPanes(), _ok_settings)
    assert rt.is_live("… Welcome to Claude Code …\n? for shortcuts")


def test_is_live_false_on_a_bare_shell():
    rt = ClaudeRuntime(NullPanes(), _ok_settings)
    assert not rt.is_live("braino@vati:~/x$ ")


def test_is_live_false_on_a_failed_launch():
    """The negative control that matters: a launch that errored is NOT live."""
    rt = ClaudeRuntime(NullPanes(), _ok_settings)
    assert not rt.is_live("claude: command not found")
