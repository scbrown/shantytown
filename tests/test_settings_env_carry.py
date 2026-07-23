"""Emitted settings must CARRY the deployment's graph config (internal-ref).

The public scrub made the graph URL and ontology namespace env-configurable —
correct for a public repo — but did not teach the emitter to EMIT them. So the
live values survived only in hand-maintained settings files, and the next
`role set` silently dropped them.

That is not hypothetical. A lead.settings.json emitted today came out with no
QUIPU_SERVER and no SHANTY_ONTO_NS, so that lead would launch pointed at the
library default: a dead localhost, and a namespace containing none of this crew's
facts. These pin the carry, and pin that no placeholder is ever invented.
"""
from __future__ import annotations
import json

import pytest

from shantytown.runtime import claude_settings_for_role


def test_carries_deployment_env_from_root_config(tmp_path):
    (tmp_path / "env.json").write_text(json.dumps({
        "QUIPU_SERVER": "http://graph.example",
        "SHANTY_ONTO_NS": "http://ns.example/ontology/",
    }))

    env = claude_settings_for_role("lead", root=tmp_path)["env"]

    assert env["QUIPU_SERVER"] == "http://graph.example"
    assert env["SHANTY_ONTO_NS"] == "http://ns.example/ontology/"
    assert env["BOBBIN_ROLE"] == "lead"


def test_every_role_carries_it_not_just_some(tmp_path):
    """The bug was role-shaped in practice: worker and administrator had the vars
    (hand-maintained) and the freshly-emitted lead did not."""
    (tmp_path / "env.json").write_text(json.dumps({"QUIPU_SERVER": "http://graph.example"}))

    for role in ("worker", "lead", "administrator"):
        env = claude_settings_for_role(role, root=tmp_path)["env"]
        assert env["QUIPU_SERVER"] == "http://graph.example", f"{role} dropped it"


def test_falls_back_to_ambient_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("QUIPU_SERVER", "http://ambient.example")

    env = claude_settings_for_role("worker", root=tmp_path)["env"]

    assert env["QUIPU_SERVER"] == "http://ambient.example"


def test_root_config_wins_over_ambient(tmp_path, monkeypatch):
    monkeypatch.setenv("QUIPU_SERVER", "http://ambient.example")
    (tmp_path / "env.json").write_text(json.dumps({"QUIPU_SERVER": "http://deployed.example"}))

    env = claude_settings_for_role("worker", root=tmp_path)["env"]

    assert env["QUIPU_SERVER"] == "http://deployed.example"


def test_omits_the_key_entirely_when_unconfigured(tmp_path, monkeypatch):
    """The one thing worse than dropping the config is writing a plausible
    placeholder into a live settings file. Absent config -> absent key, so the
    library default applies and nothing pretends to be configured."""
    monkeypatch.delenv("QUIPU_SERVER", raising=False)
    monkeypatch.delenv("SHANTY_ONTO_NS", raising=False)

    env = claude_settings_for_role("worker", root=tmp_path)["env"]

    assert "QUIPU_SERVER" not in env and "SHANTY_ONTO_NS" not in env
    assert env == {"BOBBIN_ROLE": "worker"}


def test_unreadable_env_json_does_not_crash_the_emit(tmp_path, monkeypatch):
    """A corrupt deployment config must not take the launcher down with it — the
    settings still emit, just without the carry."""
    monkeypatch.delenv("QUIPU_SERVER", raising=False)
    # BOTH carried names, not just one: a deployment shell that exports
    # SHANTY_ONTO_NS (every crew session here does) leaked into the ambient
    # fallback and failed this test on a healthy tree.
    monkeypatch.delenv("SHANTY_ONTO_NS", raising=False)
    (tmp_path / "env.json").write_text("{ not json")

    env = claude_settings_for_role("worker", root=tmp_path)["env"]

    assert env == {"BOBBIN_ROLE": "worker"}
