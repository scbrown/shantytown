"""QuipuRegistry against a REAL quipu — the OBSERVABLE effect, not a called method.

internal-ref. The rest of the quipu tests use doubles: `Recorder` records that
`_retract`/`_knot` were CALLED with the right arguments, and `Raw` feeds set()'s
response handling a canned body. Both are real properties and NEITHER is the one
that mattered — a double cannot refuse, has no SHACL shapes, and returns success
because success is all it knows. So the whole suite reported green while every
identity write was being silently refused (no rdfs:label) and every re-parent was
a silent no-op (triple-level /retract removes nothing). The bug was found only by
running the real thing against the real server; nothing in the suite could.

    A DOUBLE CANNOT TELL YOU THE API LIED TO YOU.

These tests assert the OBSERVABLE effect: after set(), query the graph and confirm
the edge is what it should be. They run against a real quipu and are therefore
OPT-IN — the hermetic unit suite must not depend on a network service, so the
whole module SKIPS unless QUIPU_CONTRACT_SERVER names a reachable quipu:

    QUIPU_CONTRACT_SERVER=http://quipu.example pytest tests/test_quipu_contract.py

DISCRIMINATION (internal-ref acceptance — a green integration test never shown to go
red is the same trap one layer out): these were proven to go RED by making `_knot`
a silent no-op (return without POSTing — a write path that does nothing and reports
success, the exact bug the Recorder missed) and running against the real server:
the agent never appears in the graph and the assertions fail. A Recorder test
asserting "`_knot` was called with the right turtle" PASSES on that same mutation —
which is the whole point. See the internal-ref comment for the transcript.

(Note: the label half of the original bug — /knot refusing a CrewMember with no
rdfs:label — does NOT reproduce on the deployment tested, which accepts a
label-less write as `conforms:true`; the CrewMember SHACL shape is evidently not
enforced there. The retract no-op (internal-ref) DOES reproduce here. So the discriminator
is the write-actually-landed check, which is server-independent, not the label.)

Writes land under the registry's default namespace `http://shantytown.example/…`,
which holds NO real crew (verified — the live crew is under aegis.gastown.local),
under pid-unique names, and every entity is entity-retracted in teardown.
"""
from __future__ import annotations

import json
import os
import urllib.request

import pytest

from shantytown.protocols import Agent
from shantytown.quipu import ONTO, QuipuRegistry, QuipuWriteRejected

_SERVER = os.environ.get("QUIPU_CONTRACT_SERVER")

pytestmark = pytest.mark.skipif(
    not _SERVER,
    reason="set QUIPU_CONTRACT_SERVER to a reachable quipu to run the live "
    "contract tests (kept opt-in so the unit suite stays hermetic)",
)


def _entity_retract(server: str, name: str) -> None:
    """Entity-level retract — the ONLY removal that actually works on this server
    (triple-level no-ops, internal-ref). Used for teardown so a test leaves the
    sandbox namespace exactly as it found it."""
    req = urllib.request.Request(
        server + "/retract",
        data=json.dumps({"entity": ONTO + name}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except OSError:
        pass  # best-effort cleanup; a leaked test entity is inert and uniquely named


@pytest.fixture
def reg():
    """A registry pointed at the real server, and a teardown that purges every
    entity the test created. Names are pid-unique so concurrent runs never collide
    and a failed cleanup can never touch a real name."""
    created: list[str] = []
    registry = QuipuRegistry(server=_SERVER)

    def track(name: str) -> str:
        created.append(name)
        return name

    registry._track = track  # type: ignore[attr-defined]
    yield registry
    for name in created:
        _entity_retract(_SERVER, name)


def _edge(registry: QuipuRegistry, name: str) -> str | None:
    """The agent's reports_to AS THE GRAPH HOLDS IT — a fresh query, not a cached
    object. Returns None if the agent has no lead, raises LookupError if absent."""
    return registry.get(name).reports_to


def test_set_creates_the_edge_in_the_real_graph(reg):
    """The property the Recorder could never check: after set(), the GRAPH differs.
    A new worker's reports_to edge is actually present when queried back."""
    pid = os.getpid()
    boss = reg._track(f"ct{pid}boss")
    hand = reg._track(f"ct{pid}hand")

    reg.set(Agent(name=boss, role="administrator"))
    reg.set(Agent(name=hand, role="worker", reports_to=boss))

    # OBSERVABLE EFFECT, read back from the server — not "set() called _knot".
    assert _edge(reg, hand) == boss, "the reports_to edge is not in the graph"
    # And the boss exists as a root (no lead edge of its own).
    assert _edge(reg, boss) is None


def test_a_new_agent_is_actually_queryable(reg):
    """set() then all(): the written agent appears in a real query. This is the
    write-path-is-alive check the label bug needed and the suite never had."""
    pid = os.getpid()
    root = reg._track(f"ct{pid}root")
    reg.set(Agent(name=root, role="administrator"))
    names = {a.name for a in reg.all()}
    assert root in names, "a set() agent did not appear in a real graph query"


@pytest.mark.xfail(
    reason="internal-ref: quipu triple-level /retract removes nothing, so a "
    "reports_to edge cannot be re-parented in the graph. set() refuses loudly "
    "(QuipuWriteRejected) rather than leaving two supervisors. When vqy9 lands "
    "this XPASSes and strict=True turns that into a failure, forcing this marker "
    "off — the test cannot rot green.",
    strict=True,
    raises=QuipuWriteRejected,
)
def test_reparent_takes_effect_in_the_real_graph(reg):
    """The observable effect that is currently IMPOSSIBLE (internal-ref): move an
    agent from one lead to another and confirm the graph shows the NEW lead and
    not the old. Fails today because /retract no-ops, which is the honest state —
    marked xfail-strict so the day the server can re-parent, CI goes red and
    someone deletes this marker."""
    pid = os.getpid()
    a = reg._track(f"ct{pid}a")
    b = reg._track(f"ct{pid}b")
    c = reg._track(f"ct{pid}c")

    reg.set(Agent(name=a, role="administrator"))
    reg.set(Agent(name=c, role="administrator"))
    reg.set(Agent(name=b, role="worker", reports_to=a))
    assert _edge(reg, b) == a

    reg.set(Agent(name=b, role="worker", reports_to=c))  # raises today (vqy9)

    assert _edge(reg, b) == c, "re-parent did not move the edge"
    assert _edge(reg, b) != a, "the OLD supervisor edge survived — two leads"
