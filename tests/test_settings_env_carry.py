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

from shantytown.runtime import _CARRIED_ENV, claude_settings_for_role


@pytest.fixture
def unconfigured(monkeypatch):
    """No carried deployment config in the ambient environment.

    Iterates `_CARRIED_ENV` rather than naming keys, because naming them is how this
    broke twice: the "absent config -> absent key" tests hand-listed QUIPU_SERVER
    and SHANTY_ONTO_NS and were then failed on a healthy tree by
    SHANTY_CANONICAL_SOURCE — a third carried name, added later, which every crew
    shell here exports. A test that asserts on "unconfigured" has to derive what
    that MEANS from the same list the emitter reads, or the next carried name
    breaks it again.
    """
    for key in _CARRIED_ENV:
        monkeypatch.delenv(key, raising=False)


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


def test_omits_the_key_entirely_when_unconfigured(tmp_path, unconfigured):
    """The one thing worse than dropping the config is writing a plausible
    placeholder into a live settings file. Absent config -> absent key, so the
    library default applies and nothing pretends to be configured."""
    env = claude_settings_for_role("worker", root=tmp_path)["env"]

    assert not any(key in env for key in _CARRIED_ENV)
    assert env == {"BOBBIN_ROLE": "worker"}


def test_unreadable_env_json_does_not_crash_the_emit(tmp_path, unconfigured):
    """A corrupt deployment config must not take the launcher down with it — the
    settings still emit, just without the carry."""
    (tmp_path / "env.json").write_text("{ not json")

    env = claude_settings_for_role("worker", root=tmp_path)["env"]

    assert env == {"BOBBIN_ROLE": "worker"}
