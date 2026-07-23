"""QuipuRegistry.set must RE-PARENT, not accumulate supervisors (internal-ref).

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
import json
from shantytown.quipu import QuipuRegistry, QuipuWriteRejected, ONTO


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


# --- the silent-failure guards (internal-ref, measured against the live server) ---

class Raw(QuipuRegistry):
    """A registry whose HTTP layer returns a canned body, so the RESPONSE-HANDLING
    is under test rather than the request. Both bugs below were invisible because
    quipu signals refusal WITHOUT an "error" key."""

    def __init__(self, body):
        self._body = body
        self.server = "http://graph.test"
        self.timeout = 5


def _patch_http(monkeypatch, reg):
    """Route _knot/_retract's urllib through the canned body."""
    import shantytown.quipu as q

    class FakeResp:
        def __init__(self, b): self._b = json.dumps(b).encode()
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(q.urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResp(reg._body))


def test_knot_raises_on_a_shacl_refusal(monkeypatch):
    """/knot answers a refused write with conforms:false and NO error key. The
    old check keyed only on "error", so set() reported success while writing
    nothing — the reason the identity graph froze."""
    reg = Raw({"conforms": False, "violations": 1,
               "issues": [{"path": "rdfs:label", "message": "MinCount(1) not satisfied"}]})
    _patch_http(monkeypatch, reg)

    with pytest.raises(QuipuWriteRejected, match="SHACL"):
        reg._knot("@prefix a: <x> .\n")


def test_knot_accepts_a_conforming_write(monkeypatch):
    """Positive control: conforms:true must NOT raise."""
    reg = Raw({"conforms": True, "count": 2, "tx_id": 540})
    _patch_http(monkeypatch, reg)

    reg._knot("@prefix a: <x> .\n")   # no exception


def test_retract_raises_when_it_removed_nothing(monkeypatch):
    """retracted:0 with no error key is the second silent no-op. Measured: this is
    what triple-level retraction of a reports_to edge actually returns."""
    reg = Raw({"entity": "x", "retracted": 0, "tx_id": 0})
    _patch_http(monkeypatch, reg)

    with pytest.raises(QuipuWriteRejected, match="retracted NOTHING"):
        reg._retract("dearing", "reports_to", "goldblum")


def test_retract_accepts_a_real_removal(monkeypatch):
    reg = Raw({"entity": "x", "retracted": 3, "tx_id": 544})
    _patch_http(monkeypatch, reg)

    reg._retract("dearing", "reports_to", "goldblum")   # no exception
