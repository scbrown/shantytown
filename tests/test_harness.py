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
from shantytown.runtime import ClaudeRuntime, settings_for_role
from shantytown.tmux import NullPanes


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
