"""QuipuRegistry (aegis-gz57) — identity from the graph.

The registry is exercised with an injected `_query` (fixture rows), so the
projection + the load-bearing failure semantics are tested without a live graph.
The same `roles.check()` that runs over `FilesRegistry` runs over this — that is
the "quipu has not leaked into the core" guarantee.
"""

import pytest

from shantytown import roles
from shantytown.protocols import Agent, Registry
from shantytown.quipu import QuipuRegistry, QuipuUnreachable, derive_agents

# A small hierarchy: goldblum is the root (has reports, no lead) = administrator;
# ian has both a lead and a report = lead; malcolm is a leaf = worker; mayor has
# neither = orphan (no lead, not administrator).
FIXTURE = [
    {"s": f"http://aegis.gastown.local/ontology/{n}", **({"rt": f"http://aegis.gastown.local/ontology/{l}"} if l else {})}
    for n, l in [
        ("goldblum", None),
        ("ian", "goldblum"),
        ("strider", "ian"),
        ("malcolm", "goldblum"),
        ("mayor", None),
    ]
]


def _reg(rows):
    r = QuipuRegistry(server="http://test.invalid")
    r._query = lambda sparql: rows  # inject fixture rows, no HTTP
    return r


def test_role_is_derived_from_structure_not_stored():
    by = {a.name: a for a in derive_agents(FIXTURE)}
    assert by["goldblum"].role == "administrator"  # root with reports
    assert by["goldblum"].reports_to is None
    assert by["ian"].role == "lead"  # has a lead AND a report (strider)
    assert by["ian"].reports_to == "goldblum"
    assert by["strider"].role == "worker"  # leaf
    assert by["malcolm"].role == "worker"
    assert by["mayor"].role == "worker"  # no lead, no reports -> stays worker (orphan)


def test_quipu_registry_satisfies_the_Registry_protocol():
    assert isinstance(_reg(FIXTURE), Registry)


def test_get_returns_agent_or_raises_lookup():
    reg = _reg(FIXTURE)
    assert reg.get("ian").reports_to == "goldblum"
    with pytest.raises(LookupError):
        reg.get("nobody")


def test_all_RAISES_when_quipu_unreachable_never_returns_empty():
    reg = QuipuRegistry(server="http://test.invalid")

    def boom(sparql):
        raise QuipuUnreachable("down")

    reg._query = boom
    # The load-bearing distinction: unreachable is NOT an empty registry.
    with pytest.raises(QuipuUnreachable):
        reg.all()


def test_the_same_check_runs_over_quipu_and_flags_the_orphan():
    # roles.check() is registry-agnostic; running it over QuipuRegistry must give
    # the same verdicts it gives over FilesRegistry -> quipu has not leaked.
    report = roles.check(_reg(FIXTURE))
    verdicts = {r.agent: r.verdict for r in report.rows}
    assert verdicts["goldblum"] == roles.OK  # administrator root
    assert verdicts["ian"] == roles.OK
    assert verdicts["strider"] == roles.OK
    assert verdicts["mayor"] == roles.BROKEN  # ORPHAN: no lead, not administrator


def test_check_reports_cannot_tell_when_quipu_is_down():
    reg = QuipuRegistry(server="http://test.invalid")
    reg._query = lambda s: (_ for _ in ()).throw(QuipuUnreachable("down"))
    report = roles.check(reg)
    # A registry it could not read is cannot-tell, never "everyone is fine".
    assert report.verdict == roles.CANNOT_TELL


def test_set_refuses_orphan_and_cycles_at_write_time():
    reg = _reg(FIXTURE)
    # self-cycle
    with pytest.raises(ValueError):
        reg.set(Agent(name="ian", reports_to="ian"))
    # orphan: no lead + not administrator
    with pytest.raises(ValueError):
        reg.set(Agent(name="newbie", role="worker", reports_to=None))
    # transitive cycle: goldblum -> ian would close ian -> goldblum -> ian
    with pytest.raises(ValueError):
        reg.set(Agent(name="goldblum", reports_to="ian"))
    # a valid worker assignment does not raise on the guard (write itself is
    # stubbed out so no HTTP happens)
    reg._knot = lambda turtle: None
    reg.set(Agent(name="newbie", role="worker", reports_to="ian"))
