"""st CARRIES the role set — it does not interpret it (GH #37, the carry half).

The layering the whole trait model rests on:

    quipu  DESCRIBES  a role (traits)      — the deployment's declaration
    st     CARRIES    the SET, opaquely    — this file
    admin  INTERPRETS the composed traits  — not st, and not here

The moment st decides what a role set MEANS in order to launch it, the closed enum
grows back one layer down, in the hardest place to see. So the assertions here are
about PASSING A SET THROUGH unchanged, and about the one distinction the migration
depends on: "declared a set" must stay distinguishable from "not migrated yet".
"""
from __future__ import annotations

import json

from shantytown.files import FilesRegistry
from shantytown.protocols import Agent
from shantytown.quipu import derive_agents
from shantytown.runtime import ClaudeRuntime
from shantytown.tmux import NullPanes


def _rt():
    return ClaudeRuntime(NullPanes(), lambda c: f"/s/{c.role}.json")


# --- the card ----------------------------------------------------------------

def test_an_unmigrated_card_has_an_EMPTY_set_not_its_tree_position(tmp_path):
    """The distinction the partial migration needs. If an un-migrated card read
    ('worker',), "has this been migrated?" would be unanswerable — and st had to
    read the field BEFORE members were bulk-migrated, or the migration writes data
    no consumer uses."""
    (tmp_path / "ellie.json").write_text(json.dumps({"role": "worker"}))
    card = FilesRegistry(tmp_path).get("ellie")
    assert card.roles == ()
    assert card.effective_roles() == ("worker",), "but it still ACTS as a worker"


def test_a_declared_set_round_trips(tmp_path):
    reg = FilesRegistry(tmp_path)
    reg.set(Agent(name="arnold", role="lead", reports_to="dearing",
                  roles=("worker", "keeper", "escalation-target"),
                  domain="security"))
    back = reg.get("arnold")
    assert back.roles == ("worker", "keeper", "escalation-target")
    assert back.domain == "security"
    assert back.effective_roles() == back.roles


def test_a_role_set_does_not_ERASE_a_declared_stack(tmp_path):
    """`roles set` owns the TREE POSITION. The stack comes from another source, so
    a tier change must not silently drop it — the same rule model/workspace/harness
    already follow."""
    reg = FilesRegistry(tmp_path)
    reg.set(Agent(name="arnold", role="worker", roles=("worker", "keeper"),
                  domain="security"))
    from dataclasses import replace
    tier_write = replace(reg.get("arnold"), role="lead", roles=(), domain=None)
    reg.set(tier_write)                      # a tier op that carries no stack
    back = reg.get("arnold")
    assert back.role == "lead", "the tree position DID change"
    assert back.roles == ("worker", "keeper"), "and the stack survived it"
    assert back.domain == "security"


# --- the launch --------------------------------------------------------------

def test_ST_ROLES_carries_the_whole_stack():
    card = Agent(name="arnold", role="lead", reports_to="dearing",
                 roles=("escalation-target", "keeper", "worker"), domain="security")
    launch = _rt().compose(card)
    assert "ST_ROLES=escalation-target,keeper,worker" in launch
    assert "ST_ROLE_DOMAIN=security" in launch
    assert "ST_REPORTS_TO=dearing" in launch
    # ...and st drew no conclusion from any of it. BOBBIN_ROLE is still the tree
    # position, unchanged by the presence of a stack.
    assert "BOBBIN_ROLE=lead" in launch


def test_ST_ROLES_is_emitted_even_for_an_unmigrated_card():
    """An agent's view of ITSELF must not depend on whether its card has been
    migrated yet — otherwise every consumer needs the fallback too."""
    assert "ST_ROLES=worker" in _rt().compose(Agent(name="ellie", role="worker"))


def test_absent_optional_values_are_OMITTED_not_emitted_empty():
    """An empty env var reads as a declared empty answer. `ST_ROLE_DOMAIN=` would
    say this agent keeps a domain called nothing."""
    launch = _rt().compose(Agent(name="ellie", role="worker"))
    assert "ST_ROLE_DOMAIN" not in launch
    assert "ST_REPORTS_TO" not in launch


def test_the_set_is_passed_through_VERBATIM():
    """No sorting, no dedup, no normalisation. st is not the authority on this
    value and must not become one by tidying it: a set that comes back in a
    different order than the graph declared is a set st has an opinion about."""
    card = Agent(name="x", role="worker", roles=("zebra", "alpha", "zebra"))
    assert "ST_ROLES=zebra,alpha,zebra" in _rt().compose(card)


# --- the graph projection (pure; the live shape replayed) --------------------

def test_derive_agents_is_unchanged_by_the_stack():
    """The roster projection still answers tree position from tree SHAPE. The
    stack is an enrichment attached afterwards, never an input to the derivation —
    a member that stacks 'administrator' is not thereby the root."""
    rows = [{"s": "http://o/admin"},
            {"s": "http://o/dee", "rt": "http://o/admin"}]
    agents = {a.name: a.role for a in derive_agents(rows)}
    assert agents == {"admin": "administrator", "dee": "worker"}
