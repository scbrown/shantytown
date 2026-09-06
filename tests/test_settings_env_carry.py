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


# ---------------------------------------------------------------------------
# THE REMOVAL PATH (aegis-936dop). Every test above renders ONCE from a clean
# fixture, so none of them could express the question that matters: `env` is a
# MERGED container, and the stale key only exists on the SECOND emission — which
# is the only kind production ever performs.
#
# Every arm here renders TWICE against the real ClaudeHarness.render, because
# the latch lives in the MERGE, not in the emitter. Asking the emitter what it
# would emit (as `test_omits_the_key_entirely_when_unconfigured` does, correctly,
# for its own question) proves nothing about what ends up on disk.
# ---------------------------------------------------------------------------

def _render_twice(monkeypatch, first_env: dict, second_env: dict,
                  existing_extra: dict | None = None, role: str = "worker",
                  role2: str | None = None) -> dict:
    """Emit with `first_env`, then re-emit with `second_env` OVER that output.

    `existing_extra` is merged into the operator's file before the second
    emission, so an arm can plant something of the operator's and prove we did
    not take it.
    """
    import json as _json
    from shantytown.harness import ClaudeHarness
    harness = ClaudeHarness()

    _unset_carried(monkeypatch)
    for key, value in first_env.items():
        monkeypatch.setenv(key, value)
    first = harness.render(claude_settings_for_role(role), "")

    if existing_extra:
        doc = _json.loads(first)
        doc.setdefault("env", {}).update(existing_extra)
        first = _json.dumps(doc, indent=2, sort_keys=True)

    _unset_carried(monkeypatch)
    for key, value in second_env.items():
        monkeypatch.setenv(key, value)
    return _json.loads(harness.render(claude_settings_for_role(role2 or role), first))


@pytest.mark.parametrize("carried", _CARRIED_ENV)
def test_a_carried_key_the_deployment_STOPS_asking_for_is_REMOVED(monkeypatch, carried):
    """The latch, per key and driven off the constant, so a fourth carried name
    cannot be added without an arm.

    Measured before the fix: all three survived at their OLD values. That is the
    dangerous kind of wrong rather than a merely stale one — `_settings_env`'s own
    docstring records that a wrong-but-REACHABLE graph or namespace answers
    "nobody exists" with a straight face, where an unreachable one at least
    raises, and SHANTY_CANONICAL_SOURCE is the pin `st doctor` audits against.
    """
    out = _render_twice(monkeypatch, {carried: "http://old.example"}, {})
    assert carried not in out["env"], (
        f"{carried} latched at its old value: st stopped emitting it and the "
        f"merge kept it, so nothing can ever remove it. env={out['env']}")


def test_a_carried_key_that_CHANGED_takes_the_new_value(monkeypatch):
    """The common case — a repoint — must still work.

    The removal path is a `pop`, so the arm that would catch it over-reaching is
    the one where the key is still configured: if the fix ran before the merge,
    or popped unconditionally, this is what would go missing.
    """
    out = _render_twice(monkeypatch,
                        {"QUIPU_SERVER": "http://graph-A.example"},
                        {"QUIPU_SERVER": "http://graph-B.example"})
    assert out["env"]["QUIPU_SERVER"] == "http://graph-B.example"


def test_the_OPERATORS_OWN_env_vars_are_not_collateral(monkeypatch):
    """The trade has to be NARROW: only the keys st manages.

    Same line `_apply_mcp_approval` draws inside a server's sub-table — it pops
    its own key and leaves the rest of the operator's entry alone. A removal path
    that reconciled the whole `env` block would take variables st never wrote,
    which is the wholesale clobber `merge_one_level` exists to prevent.
    """
    out = _render_twice(monkeypatch,
                        {"QUIPU_SERVER": "http://graph-A.example"}, {},
                        existing_extra={"OPERATOR_TOKEN": "keep-me",
                                        "HTTP_PROXY": "http://proxy.example"})
    assert "QUIPU_SERVER" not in out["env"]
    assert out["env"]["OPERATOR_TOKEN"] == "keep-me"
    assert out["env"]["HTTP_PROXY"] == "http://proxy.example"


def test_BOBBIN_ROLE_is_replaced_on_a_role_change_and_needs_no_removal(monkeypatch):
    """The other direction on the same container: BOBBIN_ROLE is emitted
    UNCONDITIONALLY, so the merge always replaces it and it is not a latch.

    Pinned because it is the reason BOBBIN_ROLE is deliberately NOT in
    `_CARRIED_ENV`, and a future reader tidying the removal path to cover "all
    env keys st writes" would be making it wrong, not more thorough.
    """
    out = _render_twice(monkeypatch, {}, {}, role="worker", role2="lead")
    assert out["env"]["BOBBIN_ROLE"] == "lead"
