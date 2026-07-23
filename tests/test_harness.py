"""The harness split — WHICH agent program a card runs (shantytown/harness.py).

Claude Code was hardcoded in two places that had to agree and had no way to: the
argv in ClaudeRuntime.compose, and the settings.json FORMAT in settings_for_role.
A Harness owns both. Claude is the only implementation, so the ONE thing these
tests have to prove is that the refactor changed NOTHING:

  test_compose_is_byte_identical_for_every_existing_card_shape pins the composed
  string against literals CAPTURED FROM THE PRE-REFACTOR CODE (git HEAD before
  the change, run over the same six cards). Not "looks right" — the same bytes.

The other half is the field: a card can now SAY which harness, files.py must
round-trip it like workspace/model, and a name we do not implement must REFUSE
rather than fall back to claude. A card that asks for codex and silently gets
claude is a launch that succeeded at being the wrong thing.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown import harness as harness_mod
from shantytown.files import FilesRegistry
from shantytown.protocols import Agent
from shantytown.runtime import (ClaudeRuntime, CapabilityError, HookSpec,
                                require_capability, settings_for_role)
from shantytown.tmux import NullPanes


class _NonBlockingHarness:
    """A REGISTERED harness that cannot deliver blocking stop hooks — the harness-
    level twin of CodexRuntime, so the capability gate can be shown to CLOSE (and,
    for a worker, OPEN) through the object the CARD actually selects. Kept test-only
    and injected: harness.py deliberately ships no guessed second program (its own
    docstring forbids it), and what is under test here is the GATE, not codex flags."""
    name = "codex-test"

    def launch(self, card, settings_path, root=None):
        return f"SHANTY_AGENT={card.name} codex-test --settings {settings_path}"

    def settings(self, role, root=None):
        return {}

    def hooks(self, card):
        return HookSpec(blocking_stop=False)


# Captured from the implementation as it stood BEFORE harness.py existed, by
# running the old ClaudeRuntime.compose over these cards. If a flag, an env var,
# or the order of any of it changes, this fails — which is the entire point of a
# refactor test. (Cards: plain worker; lead with a workspace and dangerous; admin
# with a workspace. Each once with no root, once with a root.)
_CARDS = [
    Agent(name="ellie", role="worker"),
    Agent(name="malcolm", role="lead", workspace="/home/w", dangerous=True),
    Agent(name="arnold", role="administrator", workspace="/x"),
]
_BEFORE = {
    (None, "ellie"):
        "SHANTY_AGENT=ellie BOBBIN_ROLE=worker claude --no-chrome "
        "--remote-control ellie --settings /s/worker.json",
    (None, "malcolm"):
        "cd /home/w && SHANTY_AGENT=malcolm BOBBIN_ROLE=lead claude --no-chrome "
        "--remote-control malcolm --dangerously-skip-permissions "
        "--settings /s/lead.json",
    (None, "arnold"):
        "cd /x && SHANTY_AGENT=arnold BOBBIN_ROLE=administrator claude "
        "--no-chrome --remote-control arnold --settings /s/administrator.json",
    ("/tmp/r", "ellie"):
        "SHANTY_ROOT=/tmp/r SHANTY_AGENT=ellie BOBBIN_ROLE=worker claude "
        "--no-chrome --remote-control ellie --settings /s/worker.json",
    ("/tmp/r", "malcolm"):
        "cd /home/w && SHANTY_ROOT=/tmp/r SHANTY_AGENT=malcolm BOBBIN_ROLE=lead "
        "claude --no-chrome --remote-control malcolm "
        "--dangerously-skip-permissions --settings /s/lead.json",
    ("/tmp/r", "arnold"):
        "cd /x && SHANTY_ROOT=/tmp/r SHANTY_AGENT=arnold "
        "BOBBIN_ROLE=administrator claude --no-chrome --remote-control arnold "
        "--settings /s/administrator.json",
}


def _runtime(root=None):
    return ClaudeRuntime(NullPanes(), lambda c: f"/s/{c.role}.json", root=root)


@pytest.mark.parametrize("root", [None, "/tmp/r"])
@pytest.mark.parametrize("card", _CARDS, ids=[c.name for c in _CARDS])
def test_compose_is_byte_identical_for_every_existing_card_shape(card, root):
    """THE REFACTOR TEST. Every existing card composes the same bytes it did
    before the harness split. Nine agents are running on this host; a launcher
    that quietly dropped --no-chrome or reordered the env would be discovered the
    next time one of them was restarted, by hand, at the worst moment."""
    assert _runtime(root).compose(card) == _BEFORE[(root, card.name)]


def test_a_card_with_no_harness_field_is_claude():
    """Every card in existence today. The default is not a fallback for an
    unrecognised value — it is the answer for an UNSET one."""
    assert harness_mod.name_for(Agent(name="ellie")) == "claude"
    assert harness_mod.for_card(Agent(name="ellie")).name == "claude"
    assert harness_mod.get(None).name == "claude"


def test_the_settings_format_is_the_harness_s_and_is_unchanged():
    """settings_for_role now DISPATCHES to the harness. Same file, same bytes:
    the emitted settings are what nine live agents' hooks are wired from."""
    from shantytown.runtime import claude_settings_for_role
    for role in ("worker", "lead", "administrator"):
        via_harness = harness_mod.get("claude").settings(role, root="/tmp/r")
        assert settings_for_role(role, root="/tmp/r") == via_harness
        assert via_harness == claude_settings_for_role(role, root="/tmp/r")
        # and it is still Claude Code's schema, not a generic one
        assert "Stop" in via_harness["hooks"]


def test_an_unimplemented_harness_is_refused_not_defaulted(tmp_path):
    """A card naming a harness we do not ship must NOT launch claude. Silently
    substituting a different program is the failure this whole file exists to
    prevent — and it would report success."""
    card = Agent(name="ellie", role="worker", harness="codex")
    with pytest.raises(harness_mod.UnknownHarness):
        _runtime().compose(card)


# --- the capability declaration lives on the harness now (internal-ref) ------------

def test_claude_harness_declares_the_blocking_stop_capability():
    """The declaration MOVED here, onto the program the card selects. Claude Code
    delivers blocking stop hooks, so a lead/administrator on it is hostable — and
    the gate, asked the harness directly, opens for it."""
    h = harness_mod.ClaudeHarness()
    assert h.hooks(Agent(name="x", role="lead")).blocking_stop is True
    require_capability(h, Agent(name="x", role="administrator"))    # must not raise


def test_claude_runtime_hooks_FORWARD_to_the_cards_harness():
    """ClaudeRuntime no longer declares blocking_stop itself — it forwards to the
    card's harness, so there is ONE source of truth and the two cannot drift (the
    drift that let the gate rubber-stamp a non-claude card). A card naming a
    non-blocking harness makes the RUNTIME report non-blocking too."""
    rt = _runtime()
    assert rt.hooks(Agent(name="x", role="lead")).blocking_stop is True   # claude
    # forwards to whatever the card names:
    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as m:
        m.setitem(harness_mod._HARNESSES, "codex-test", _NonBlockingHarness())
        card = Agent(name="x", role="lead", harness="codex-test")
        assert rt.hooks(card).blocking_stop is False


@pytest.mark.parametrize("role,hostable", [("worker", True), ("lead", False),
                                           ("administrator", False)])
def test_the_gate_asks_the_CARDS_harness_not_the_runtime(monkeypatch, role, hostable):
    """THE FIX. Build a ClaudeRuntime (blocking_stop=True) — exactly what the CLI
    always builds — but give the card a NON-blocking harness. Before internal-ref the
    gate asked `self` and rubber-stamped the lead; now it asks the program the card
    NAMES and refuses. The worker is the positive control: the gate must still OPEN
    for a role that needs no stop delivery, and the launch must carry the CARD's
    program, proving compose went through card.harness rather than claude's argv."""
    monkeypatch.setitem(harness_mod._HARNESSES, "codex-test", _NonBlockingHarness())
    card = Agent(name="malcolm", role=role, harness="codex-test")
    rt = _runtime()
    if hostable:
        assert "codex-test --settings" in rt.compose(card)         # gate OPENS
    else:
        with pytest.raises(CapabilityError, match="blocking stop hooks"):
            rt.compose(card)                                       # gate CLOSES


def test_the_card_round_trips_the_harness_field(tmp_path: Path):
    """files.py must read and preserve `harness` exactly like workspace/model —
    including across a `role set`, which does not own the field."""
    crew = tmp_path / "crew"
    crew.mkdir()
    (crew / "ellie.json").write_text(json.dumps(
        {"role": "worker", "harness": "claude", "workspace": "/w"}))
    reg = FilesRegistry(crew)
    assert reg.get("ellie").harness == "claude"

    # A role change must not wipe it (the model/#9 bug, one field over).
    reg.set(Agent(name="ellie", role="lead"))
    assert reg.get("ellie").harness == "claude"
    assert json.loads((crew / "ellie.json").read_text())["harness"] == "claude"


def test_a_card_without_the_field_stays_without_it(tmp_path: Path):
    """The default is not written into every card on the next `role set`. A
    persisted "claude" would be a claim the card never made, and the fleet's
    cards are read by humans."""
    crew = tmp_path / "crew"
    crew.mkdir()
    (crew / "ian.json").write_text(json.dumps({"role": "worker"}))
    reg = FilesRegistry(crew)
    reg.set(Agent(name="ian", role="worker"))
    assert "harness" not in json.loads((crew / "ian.json").read_text())
    assert harness_mod.name_for(reg.get("ian")) == "claude"
