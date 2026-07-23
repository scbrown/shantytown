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


class QuipuWriteRejected(Exception):
    """quipu REACHED, understood, and REFUSED the write — a SHACL violation.

    Distinct from QuipuUnreachable on purpose: "I could not look" and "I looked,
    and the graph told me no" are different answers, and the caller's remedy
    differs (retry/escalate vs fix the payload). Both used to be invisible: /knot
    reports a refusal as {"conforms": false} with NO "error" key, so the write
    path swallowed it and reported success."""


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
        the invalid state never enters the graph a projection would then copy.

        RE-PARENTING IS A RETRACT, THEN AN ASSERT (internal-ref). `_knot` only ADDS
        turtle, and `reports_to` is not functional in the store, so asserting a
        new lead without retracting the old one leaves BOTH edges in the graph.
        The agent then has two supervisors, and `derive_agents` sees a shape that
        cannot occur in a real org.

        This is not a hypothetical: it is almost certainly why the graph and the
        cards diverged in the first place. Every role change had to be made on the
        card, because making it in the graph would have corrupted the graph — so
        the "source of truth" became the one place nobody could safely write, and
        drifted for it. A source of truth you cannot update is a document.
        """
        if agent.reports_to == agent.name:
            raise ValueError(f"refused: {agent.name} would report to itself (cycle)")
        if agent.reports_to is None and agent.role != "administrator":
            raise ValueError(
                f"refused: {agent.name} has no lead and is not an administrator (orphan)"
            )
        # reports_to is a graph edge; role is derived, so we assert the edge, not
        # a role literal. Administrators (root) carry no reports_to edge.
        # Retract any EXISTING reports_to edge first, so a re-parent replaces the
        # supervisor instead of adding a second one. Done before the cycle check
        # below reads the graph, so the check sees the shape we are actually
        # heading for rather than the stale one.
        try:
            current = self.get(agent.name)
        except LookupError:
            current = None
        if current is not None and current.reports_to not in (None, agent.reports_to):
            self._retract(agent.name, "reports_to", current.reports_to)

        # rdfs:label is REQUIRED by the graph's SHACL shape for a CrewMember
        # (MinCount(1)). Omitting it is why every identity write this registry ever
        # made was refused — silently, because /knot answers a refusal without an
        # "error" key. The label is the agent's name: that is what the shape asks
        # for and what every hand-written crew episode already carries.
        triples = [
            f"a:{agent.name} a a:CrewMember .",
            f'a:{agent.name} rdfs:label "{agent.name}" .',
        ]
        if agent.reports_to is not None:
            # cycle guard beyond the trivial self-edge: refuse if the new lead
            # already reaches back to this agent through the existing graph.
            if self._reaches(agent.reports_to, agent.name):
                raise ValueError(
                    f"refused: {agent.name} -> {agent.reports_to} closes a reporting cycle"
                )
            triples.append(f"a:{agent.name} a:reports_to a:{agent.reports_to} .")
        turtle = (f"@prefix a: <{ONTO}> .\n"
                  '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n'
                  + "\n".join(triples) + "\n")
        self._knot(turtle)

    def _retract(self, subject: str, predicate: str, obj: str) -> None:
        """Retract exactly one triple (quipu /retract, entity+predicate+value =
        triple-level). Anything coarser would take unrelated facts with it."""
        req = urllib.request.Request(
            self.server + "/retract",
            data=json.dumps({
                "entity": ONTO + subject,
                "predicate": ONTO + predicate,
                "value": ONTO + obj,
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise QuipuUnreachable(f"quipu at {self.server} unreachable: {e}") from e
        if isinstance(body, dict) and body.get("error"):
            raise QuipuUnreachable(f"quipu retract error: {body['error']}")
        # SILENT NO-OP #2, same shape as the SHACL one above. Triple-level retract
        # (entity+predicate+value) answers {"retracted": 0, "tx_id": 0} with NO
        # "error" key when it removes nothing — and MEASURED against the live
        # server on 2026-07-20, it removes nothing for a reports_to edge every
        # time. Only ENTITY-level retraction actually deletes.
        #
        # So the graph currently has no way to change one edge: you can add facts
        # and you can destroy a whole entity, and nothing in between. That is the
        # standing reason an agent cannot be re-parented in the graph, and why the
        # cards became the de-facto tier. Refusing loudly here is the honest
        # behaviour — the alternative is set() leaving TWO supervisors and
        # reporting success, which is how this stayed invisible.
        if isinstance(body, dict) and not body.get("retracted"):
            raise QuipuWriteRejected(
                f"quipu retracted NOTHING for {subject} {predicate} {obj} "
                f"(retracted=0). Triple-level retraction does not remove "
                f"reports_to edges on this server; only entity-level does. "
                f"Re-parenting {subject} in the graph is therefore not possible "
                f"without destroying and rebuilding the entity — refusing rather "
                f"than leaving it with two supervisors."
            )

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
        # A SHACL REJECTION IS NOT AN ERROR KEY (internal-ref). /knot answers a
        # refused write with {"conforms": false, "violations": N, "issues": [...]}
        # and NO "error" field — so the check above waved it through and set()
        # reported success while writing precisely nothing.
        #
        # That is the whole reason the identity graph froze and the cards became
        # the de-facto truth: every graph write silently no-opped, so every role
        # change had to be made on a card. A write path that cannot fail is not a
        # write path, and this one had never once told anybody it had failed.
        if isinstance(body, dict) and body.get("conforms") is False:
            issues = body.get("issues") or []
            detail = "; ".join(
                f"{i.get('path', '?')}: {i.get('message', '?')}" for i in issues[:3]
            ) or f"{body.get('violations', '?')} violation(s)"
            raise QuipuWriteRejected(f"quipu refused the write (SHACL): {detail}")
