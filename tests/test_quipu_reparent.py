"""QuipuRegistry.set must RE-PARENT, not accumulate supervisors (aegis-0v97).

`_knot` only adds turtle and `reports_to` is not functional in the store, so
asserting a new lead without retracting the old one left BOTH edges in the graph
and gave the agent two supervisors — a shape no real org has and `derive_agents`
cannot sensibly project.

This is very likely the root cause of the cards-vs-graph divergence that 0v97 is
about: every role change had to be made on the CARD, because making it in the
GRAPH would have corrupted the graph. The declared source of truth was the one
place nobody could safely write, so it drifted. These pin the fix.
"""
from __future__ import annotations

import pytest

from shantytown.protocols import Agent
from shantytown.quipu import QuipuRegistry, ONTO


class Recorder(QuipuRegistry):
    """A QuipuRegistry whose graph is a dict and whose I/O is a log. Subclassing
    (rather than mocking urllib) keeps set()'s real logic under test — the retract
    decision is the thing being pinned, not the HTTP."""

    def __init__(self, agents):
        self._agents = list(agents)
        self.knots: list[str] = []
        self.retracts: list[tuple[str, str, str]] = []

    def all(self):
        return list(self._agents)

    def _knot(self, turtle):
        self.knots.append(turtle)

    def _retract(self, subject, predicate, obj):
        self.retracts.append((subject, predicate, obj))


def test_reparent_retracts_the_old_edge_before_asserting_the_new_one():
    r = Recorder([Agent(name="dearing", role="worker", reports_to="goldblum"),
                  Agent(name="sattler", role="administrator")])

    r.set(Agent(name="dearing", role="lead", reports_to="sattler"))

    assert r.retracts == [("dearing", "reports_to", "goldblum")], \
        "the stale supervisor edge must be retracted, or dearing has two leads"
    assert any("a:dearing a:reports_to a:sattler" in t for t in r.knots)


def test_unchanged_supervisor_retracts_nothing():
    """Idempotence: re-asserting the same edge must not churn the graph."""
    r = Recorder([Agent(name="dearing", role="worker", reports_to="sattler"),
                  Agent(name="sattler", role="administrator")])

    r.set(Agent(name="dearing", role="lead", reports_to="sattler"))

    assert r.retracts == []


def test_new_agent_retracts_nothing():
    r = Recorder([Agent(name="sattler", role="administrator")])

    r.set(Agent(name="zia", role="worker", reports_to="sattler"))

    assert r.retracts == []
    assert any("a:zia a:reports_to a:sattler" in t for t in r.knots)


def test_promotion_to_root_retracts_the_old_edge_and_asserts_none():
    """administrator = no reports_to edge at all. The old edge must still go, or
    the new root keeps a supervisor and is not a root."""
    r = Recorder([Agent(name="sattler", role="worker", reports_to="goldblum")])

    r.set(Agent(name="sattler", role="administrator", reports_to=None))

    assert r.retracts == [("sattler", "reports_to", "goldblum")]
    assert not any("a:reports_to" in t for t in r.knots)


def test_still_refuses_an_orphan_and_writes_nothing():
    """The pre-existing guards must survive the change — and critically, a refusal
    must not have already retracted the old edge on its way to raising."""
    r = Recorder([Agent(name="dearing", role="worker", reports_to="goldblum")])

    with pytest.raises(ValueError, match="orphan"):
        r.set(Agent(name="dearing", role="worker", reports_to=None))

    assert r.retracts == [] and r.knots == []


def test_still_refuses_a_self_cycle_and_writes_nothing():
    r = Recorder([Agent(name="dearing", role="worker", reports_to="goldblum")])

    with pytest.raises(ValueError, match="cycle"):
        r.set(Agent(name="dearing", role="worker", reports_to="dearing"))

    assert r.retracts == [] and r.knots == []
