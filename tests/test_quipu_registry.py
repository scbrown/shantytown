"""QuipuRegistry — identity from the graph.

The registry is exercised with an injected `_query` (fixture rows), so the
projection + the load-bearing failure semantics are tested without a live graph.
The same `roles.check()` that runs over `FilesRegistry` runs over this — that is
the "quipu has not leaked into the core" guarantee.
"""

import pytest

from shantytown import roles
from shantytown.protocols import Agent, Registry
from shantytown.quipu import ONTO, QuipuRegistry, QuipuUnreachable, derive_agents

# A small hierarchy: hammond is the root (has reports, no lead) = administrator;
# ian has both a lead and a report = lead; malcolm is a leaf = worker; mayor has
# neither = orphan (no lead, not administrator).
FIXTURE = [
    # Built from the module's own ONTO so the namespace has ONE definition. A
    # second hardcoded copy here would keep passing after a deployment repointed
    # SHANTY_ONTO_NS, which is the fragmentation this constant exists to control.
    {"s": f"{ONTO}{n}", **({"rt": f"{ONTO}{l}"} if l else {})}
    for n, l in [
        ("hammond", None),
        ("ian", "hammond"),
        ("strider", "ian"),
        ("malcolm", "hammond"),
        ("mayor", None),
    ]
]


def _reg(rows):
    r = QuipuRegistry(server="http://test.invalid")
    r._query = lambda sparql: rows  # inject fixture rows, no HTTP
    return r


def test_role_is_derived_from_structure_not_stored():
    by = {a.name: a for a in derive_agents(FIXTURE)}
    assert by["hammond"].role == "administrator"  # root with reports
    assert by["hammond"].reports_to is None
    assert by["ian"].role == "lead"  # has a lead AND a report (strider)
    assert by["ian"].reports_to == "hammond"
    assert by["strider"].role == "worker"  # leaf
    assert by["malcolm"].role == "worker"
    assert by["mayor"].role == "worker"  # no lead, no reports -> stays worker (orphan)


def test_quipu_registry_satisfies_the_Registry_protocol():
    assert isinstance(_reg(FIXTURE), Registry)


def test_get_returns_agent_or_raises_lookup():
    reg = _reg(FIXTURE)
    assert reg.get("ian").reports_to == "hammond"
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
    assert verdicts["hammond"] == roles.OK  # administrator root
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
    # transitive cycle: hammond -> ian would close ian -> hammond -> ian
    with pytest.raises(ValueError):
        reg.set(Agent(name="hammond", reports_to="ian"))
    # a valid worker assignment does not raise on the guard (write itself is
    # stubbed out so no HTTP happens)
    reg._knot = lambda turtle: None
    reg.set(Agent(name="newbie", role="worker", reports_to="ian"))


# --- bearer auth (the client half of the quipu write-auth flip) ---------------

def test_headers_carry_bearer_only_when_env_token_is_set(monkeypatch, tmp_path):
    from shantytown.quipu import request_headers

    # Isolate from the HOST's real token file — this test is about the env
    # var, and a developer machine that has done the z10 flip carries
    # ~/.config/quipu/token, which the fallback would (correctly) read.
    monkeypatch.setenv("QUIPU_AUTH_TOKEN_FILE", str(tmp_path / "absent"))

    # Unset: today's open-server behaviour, byte-identical headers.
    monkeypatch.delenv("QUIPU_AUTH_TOKEN", raising=False)
    assert request_headers() == {"Content-Type": "application/json"}

    # Empty: must NOT become `Bearer ` — an empty env var is unset, not a
    # (wrong) credential that turns a misconfiguration into 401s.
    monkeypatch.setenv("QUIPU_AUTH_TOKEN", "")
    assert "Authorization" not in request_headers()

    # Set: every request carries the bearer.
    monkeypatch.setenv("QUIPU_AUTH_TOKEN", "sekrit")
    assert request_headers()["Authorization"] == "Bearer sekrit"


def test_token_file_is_the_fallback_env_the_override(monkeypatch, tmp_path):
    from shantytown.quipu import request_headers

    tok = tmp_path / "token"
    tok.write_text("file-tok\n")  # trailing newline must be stripped
    monkeypatch.setenv("QUIPU_AUTH_TOKEN_FILE", str(tok))

    # File alone: used (reaches sessions launched before the env existed).
    monkeypatch.delenv("QUIPU_AUTH_TOKEN", raising=False)
    assert request_headers()["Authorization"] == "Bearer file-tok"

    # Env set: overrides the file.
    monkeypatch.setenv("QUIPU_AUTH_TOKEN", "env-tok")
    assert request_headers()["Authorization"] == "Bearer env-tok"

    # Neither readable: no header, open-server behaviour.
    monkeypatch.delenv("QUIPU_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("QUIPU_AUTH_TOKEN_FILE", str(tmp_path / "absent"))
    assert "Authorization" not in request_headers()


def test_all_query_excludes_crewStatus_bearing_members():
    """aegis-wxrm: retired / never-instantiated CrewMembers carry a:crewStatus
    (absence = active). The exclusion lives IN THE QUERY so every consumer of
    all()/get() — including `st project` — inherits it; without it a projection
    mints mayor/strider/walker cards, and a mayor card is the black-hole
    dispatch recipient this fleet retired (aegis-rvhn). Injected backends
    bypass SPARQL, so the contract is pinned on the query text itself."""
    seen = []
    r = QuipuRegistry(server="http://test.invalid")
    r._query = lambda sparql: seen.append(sparql) or []
    try:
        r.all()
    except Exception:
        pass  # empty rows are fine; we care about the query text
    assert seen, "all() never issued its query"
    q = " ".join(seen[0].split())
    # Pin the OPTIONAL/!bound FORM, not just intent: quipu's engine REJECTS
    # `FILTER NOT EXISTS` ("unsupported FILTER expression"), and _query raises
    # on an error body — so the modern form makes every all() call read as
    # quipu-unreachable. Verified against the live engine 2026-07-23: this form
    # returns 18 members, the NOT EXISTS form returns an error.
    assert "OPTIONAL { ?s a:crewStatus ?cs } FILTER(!bound(?cs))" in q, \
        "the crewStatus exclusion left the query — retired members would project again"
    assert "NOT EXISTS" not in q, \
        "FILTER NOT EXISTS is UNSUPPORTED by quipu's engine — this query would raise QuipuUnreachable on every call"
