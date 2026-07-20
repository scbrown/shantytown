"""quipu — identity from the graph. The source of truth.

Stiwi: "quipu should be the source of truth." The hierarchy is a **query, not a
thing to store**: the graph holds `aegis:reports_to` edges, and role
(worker / lead / administrator) is *derived* from the shape of those edges — it
is a projection, never a stored field. See `docs/agent-card.md`.

This is the first-class identity backend; `FilesRegistry` (shantytown 1) is the
second impl, and it exists to prove quipu has not leaked into the core — the
same `roles.check()` runs over both.

The load-bearing property (and the reason `roles --check` has an exit-2 path):
`all()` **raises** when quipu is unreachable. It never returns `[]` on failure.
"nobody exists" and "I could not look" are DIFFERENT ANSWERS — collapsing them
is exactly the "reported CLEAR when it couldn't reach its target" bug
. An errored query is not a zero-result; it is NO result.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .protocols import Agent

# The ontology IRI base. THIS IS DATA IDENTITY, NOT COSMETICS: every triple in a
# graph is keyed under it, so a deployment that changes this value stops joining
# its own existing facts — new entities land beside the old ones instead of on
# them, and nothing errors. Set SHANTY_ONTO_NS once, per graph, and never again.
# Read at import time because the SPARQL below is built at class-definition time.
ONTO = os.environ.get("SHANTY_ONTO_NS") or "http://shantytown.example/ontology/"


class QuipuUnreachable(Exception):
    """quipu could not be reached or returned an error. NOT 'nobody exists' —
    'I could not look'. Callers must surface this as cannot-tell / exit 2, never
    swallow it into an empty registry."""


def _local(iri: str) -> str:
    """The local name of an aegis IRI (`…/ontology/ian` -> `ian`)."""
    return iri.rsplit("/", 1)[-1] if iri.startswith("http") else iri


def derive_agents(rows: list[dict]) -> list[Agent]:
    """Project `[{s, rt?}]` crew rows into Agents with a DERIVED role.

    Pure function (no I/O), so the projection is testable without a live graph.
    Role is the shape of the hierarchy:

      - has reports (someone reports to it) + no lead      -> administrator (root)
      - has reports + a lead                               -> lead
      - no reports + a lead                                -> worker
      - no reports + no lead                               -> worker  (an ORPHAN;
            role stays worker so `roles.check` flags it BROKEN, since only an
            `administrator` may legitimately report to nobody)
    """
    reports_to: dict[str, str | None] = {}
    for r in rows:
        name = _local(r["s"])
        reports_to.setdefault(name, None)
        rt = r.get("rt")
        if rt:
            reports_to[name] = _local(rt)
    has_reports = {rt for rt in reports_to.values() if rt is not None}

    agents: list[Agent] = []
    for name, lead in sorted(reports_to.items()):
        if lead is None and name in has_reports:
            role = "administrator"
        elif name in has_reports:
            role = "lead"
        else:
            role = "worker"
        agents.append(Agent(name=name, role=role, reports_to=lead))
    return agents


class QuipuRegistry:
    """Identity from the quipu graph. get / all / set over `aegis:CrewMember`."""

    _ALL = (
        f"PREFIX a: <{ONTO}> "
        "SELECT ?s ?rt WHERE { ?s a a:CrewMember . OPTIONAL { ?s a:reports_to ?rt } }"
    )

    def __init__(self, server: str | None = None, timeout: float = 5.0):
        # QUIPU_SERVER is the variable the crew hooks already use. The default is
        # a local quipu-server, not any particular deployment's hostname.
        self.server = server or os.environ.get("QUIPU_SERVER") or "http://localhost:3030"
        self.timeout = timeout

    def _query(self, sparql: str) -> list[dict]:
        """POST a SPARQL query; return its rows. Raises `QuipuUnreachable` on a
        connection failure OR an error body — an errored query is NO result."""
        req = urllib.request.Request(
            self.server + "/query",
            data=json.dumps({"query": sparql}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise QuipuUnreachable(f"quipu at {self.server} unreachable: {e}") from e
        if isinstance(body, dict) and body.get("error"):
            raise QuipuUnreachable(f"quipu query error: {body['error']}")
        return body.get("rows", []) if isinstance(body, dict) else []

    def all(self) -> list[Agent]:
        """Every crew member, roles derived. RAISES `QuipuUnreachable` if quipu
        cannot be read — never returns `[]` on failure."""
        return derive_agents(self._query(self._ALL))

    def get(self, name: str) -> Agent:
        """One agent by name. Raises `LookupError` if absent (a real answer),
        `QuipuUnreachable` if quipu can't be read (not an answer)."""
        for a in self.all():
            if a.name == name:
                return a
        raise LookupError(f"no such agent in quipu: {name}")

    def set(self, agent: Agent) -> None:
        """Write the identity to the graph — the source of truth. Refuses an
        ORPHAN (no lead, not an administrator) and a self-cycle AT WRITE TIME, so
        the invalid state never enters the graph a projection would then copy."""
        if agent.reports_to == agent.name:
            raise ValueError(f"refused: {agent.name} would report to itself (cycle)")
        if agent.reports_to is None and agent.role != "administrator":
            raise ValueError(
                f"refused: {agent.name} has no lead and is not an administrator (orphan)"
            )
        # reports_to is a graph edge; role is derived, so we assert the edge, not
        # a role literal. Administrators (root) carry no reports_to edge.
        triples = [f"a:{agent.name} a a:CrewMember ."]
        if agent.reports_to is not None:
            # cycle guard beyond the trivial self-edge: refuse if the new lead
            # already reaches back to this agent through the existing graph.
            if self._reaches(agent.reports_to, agent.name):
                raise ValueError(
                    f"refused: {agent.name} -> {agent.reports_to} closes a reporting cycle"
                )
            triples.append(f"a:{agent.name} a:reports_to a:{agent.reports_to} .")
        turtle = f"@prefix a: <{ONTO}> .\n" + "\n".join(triples) + "\n"
        self._knot(turtle)

    def _reaches(self, start: str, target: str) -> bool:
        """Does `start` reach `target` by following reports_to (cycle check)?"""
        seen: set[str] = set()
        agents = {a.name: a for a in self.all()}
        cur: str | None = start
        while cur is not None and cur not in seen:
            if cur == target:
                return True
            seen.add(cur)
            a = agents.get(cur)
            cur = a.reports_to if a else None
        return False

    def _knot(self, turtle: str) -> None:
        req = urllib.request.Request(
            self.server + "/knot",
            data=json.dumps({"turtle": turtle}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise QuipuUnreachable(f"quipu at {self.server} unreachable: {e}") from e
        if isinstance(body, dict) and body.get("error"):
            raise QuipuUnreachable(f"quipu write error: {body['error']}")
