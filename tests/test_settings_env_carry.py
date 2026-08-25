"""Emitted settings must CARRY the deployment's graph config (aegis-0v97).

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
import pytest

from shantytown.runtime import _CARRIED_ENV, claude_settings_for_role


def _unset_carried(monkeypatch):
    """Clear EVERY carried name, driven off the constant itself.

    The "absent config -> absent key" tests read the ambient env as their
    fallback, so any carried name a crew shell exports leaks straight into them.
    This was hand-listed and went stale twice: SHANTY_ONTO_NS was added, then
    SHANTY_CANONICAL_SOURCE — and the second one failed both tests on a healthy
    tree for every agent in this fleet, whose sessions all export it. Reading
    _CARRIED_ENV means adding a fourth carried name cannot reintroduce it.
    """
    for key in _CARRIED_ENV:
        monkeypatch.delenv(key, raising=False)


def _deployment(root, **env):
    lines = ["[env]", *(f'{key} = "{value}"' for key, value in env.items())]
    (root / "shantytown.toml").write_text("\n".join(lines) + "\n")


def test_carries_deployment_env_from_root_config(tmp_path):
    _deployment(tmp_path, QUIPU_SERVER="http://graph.example",
                SHANTY_ONTO_NS="http://ns.example/ontology/")

    env = claude_settings_for_role("lead", root=tmp_path)["env"]

    assert env["QUIPU_SERVER"] == "http://graph.example"
    assert env["SHANTY_ONTO_NS"] == "http://ns.example/ontology/"
    assert env["BOBBIN_ROLE"] == "lead"


def test_every_role_carries_it_not_just_some(tmp_path):
    """The bug was role-shaped in practice: worker and administrator had the vars
    (hand-maintained) and the freshly-emitted lead did not."""
    _deployment(tmp_path, QUIPU_SERVER="http://graph.example")

    for role in ("worker", "lead", "administrator"):
        env = claude_settings_for_role(role, root=tmp_path)["env"]
        assert env["QUIPU_SERVER"] == "http://graph.example", f"{role} dropped it"


def test_falls_back_to_ambient_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("QUIPU_SERVER", "http://ambient.example")

    env = claude_settings_for_role("worker", root=tmp_path)["env"]

    assert env["QUIPU_SERVER"] == "http://ambient.example"


def test_root_config_wins_over_ambient(tmp_path, monkeypatch):
    monkeypatch.setenv("QUIPU_SERVER", "http://ambient.example")
    _deployment(tmp_path, QUIPU_SERVER="http://deployed.example")

    env = claude_settings_for_role("worker", root=tmp_path)["env"]

    assert env["QUIPU_SERVER"] == "http://deployed.example"


def test_omits_the_key_entirely_when_unconfigured(tmp_path, monkeypatch):
    """The one thing worse than dropping the config is writing a plausible
    placeholder into a live settings file. Absent config -> absent key, so the
    library default applies and nothing pretends to be configured."""
    _unset_carried(monkeypatch)

    env = claude_settings_for_role("worker", root=tmp_path)["env"]

    assert "QUIPU_SERVER" not in env and "SHANTY_ONTO_NS" not in env
    assert env == {"BOBBIN_ROLE": "worker"}


def test_unreadable_toml_does_not_crash_the_emit(tmp_path, monkeypatch):
    """A corrupt deployment config must not take the launcher down with it — the
    settings still emit, just without the carry."""
    _unset_carried(monkeypatch)
    (tmp_path / "shantytown.toml").write_text("[env\nbroken")

    env = claude_settings_for_role("worker", root=tmp_path)["env"]

    assert env == {"BOBBIN_ROLE": "worker"}
