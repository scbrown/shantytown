"""graph_adoption — make graph context a REQUIREMENT and a MEASUREMENT.

`aegis-x6yoq` shipped `--quipu-node` on `st go` and `st cycle`, so a dispatch
*can* carry what the graph already knows. This module is the half that decides
whether anyone does it: an eligible dispatch carries an exact existing node or a
stated `no_graph_context` reason, and every decision lands in a ledger so the
denominator exists (aegis-rcyd.1, under aegis-rcyd's "instrument usage, drive
adoption").

Three design choices, each of which has a failure mode behind it:

1. **A missing node REFUSES; an unreachable graph does NOT.** Naming a node that
   does not exist is a claim about the graph, and letting it through is how a
   dispatch cites something nobody can look up. But an outage is not the
   dispatcher's fault, and a coordinator blocked from handing out work because a
   knowledge graph is down is a far worse failure than an unverified node. So:
   verified-absent is a refusal, could-not-check rides the ledger as
   `unverifiable` and the dispatch proceeds. That is the same split the rest of
   this codebase insists on — "nobody matched" and "I could not look" are
   different answers.

2. **The exemption is free text and is never validated.** `--no-graph-context
   'first pass, nothing in the graph yet'` is a legitimate answer and must cost
   one flag, because a requirement that blocks real work gets removed. What the
   ledger measures is not compliance-as-obedience but the SHAPE of the
   exemptions: if one reason dominates, that is a finding about the graph, not
   about the fleet.

3. **Nothing here mints a node.** Auto-creating an entity to satisfy a check
   would manufacture exactly the aliases the ingest rules spend pages avoiding.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

LEDGER = "graph-adoption.jsonl"

# What `verification` can hold. Kept as strings because they are written to a
# ledger that outlives this module and is read by things that are not Python.
VERIFIED = "verified"          # every named node exists in the graph
UNVERIFIABLE = "unverifiable"  # the graph could not be reached or asked
UNCHECKED = "unchecked"        # verification was not attempted (dry-run, no server)
EXEMPT = "exempt"              # no nodes named; a reason was given instead
MISSING = "missing"            # neither named nor excused, and the mode allowed it

# How hard the requirement bites. ADVISE is the default ON PURPOSE: this landed
# on a fleet whose scripts, crons and skills already call `st go`, and a flag
# that refuses every one of them the moment it is pulled is a fleet-stopping
# change dressed as a measurement. Advise warns and records `missing`, so the
# ledger measures non-compliance instead of preventing it, and the flip to
# REQUIRE is one config line once the callers carry the flag. That order is the
# fleet's own advise-then-enforce ladder, and it is also the only order in which
# the enforcement gets to be justified by evidence rather than by intent.
ADVISE, REQUIRE = "advise", "require"
MODE_ENV = "SHANTY_GRAPH_CONTEXT"


def mode(root=None) -> str:
    """`require` refuses; anything else advises. Env wins, then [graph] in the
    deployment config, then the safe default."""
    env = (os.environ.get(MODE_ENV) or "").strip().lower()
    if env in (ADVISE, REQUIRE):
        return env
    if root:
        try:
            from . import config as config_mod
            cfg, _ = config_mod.load_or_default(Path(root))
            # The deployment [env] table, which is the fleet's existing switch for
            # this kind of thing — not a new Config field, so the flip is a
            # one-line edit to a file the deployment already owns.
            value = str((cfg.env or {}).get(MODE_ENV, "")).strip().lower()
            if value in (ADVISE, REQUIRE):
                return value
        except Exception:  # noqa: BLE001 — an unreadable config must not enforce
            pass
    return ADVISE


class GraphContextMissing(Exception):
    """Neither a node nor a stated reason. The dispatch is refused."""


class GraphNodeUnknown(Exception):
    """A named node is verified ABSENT from the graph. The dispatch is refused."""


@dataclass(frozen=True)
class GraphContext:
    nodes: tuple[str, ...] = ()
    exemption: str = ""
    verification: str = UNCHECKED
    unknown: tuple[str, ...] = ()
    detail: str = ""

    @property
    def exempt(self) -> bool:
        return not self.nodes and bool(self.exemption)

    @property
    def missing(self) -> bool:
        return not self.nodes and not self.exemption

    def render(self) -> str:
        if self.missing:
            return "NO graph context and no reason given — recorded as missing"
        if self.exempt:
            return f"no graph context: {self.exemption}"
        body = ", ".join(self.nodes)
        if self.verification == VERIFIED:
            return f"graph: {body} (verified)"
        if self.verification == UNVERIFIABLE:
            return f"graph: {body} (UNVERIFIED — {self.detail or 'graph unreachable'})"
        return f"graph: {body}"


def require(nodes, exemption: str = "") -> GraphContext:
    """Turn the raw flags into a context, or raise GraphContextMissing.

    Refusing here rather than at the send is deliberate: the caller has not yet
    typed into anyone's pane, so a refusal costs nothing and a dispatch that
    reaches an agent without provenance cannot be taken back.
    """
    clean = tuple(n.strip() for n in (nodes or []) if n and n.strip())
    reason = (exemption or "").strip()
    if clean:
        # Both given is not an error — the reason then annotates a partial
        # answer ("one node, the rest isn't modelled yet") and is worth keeping.
        return GraphContext(nodes=clean, exemption=reason)
    if reason:
        return GraphContext(exemption=reason, verification=EXEMPT)
    raise GraphContextMissing(
        "no graph context. Name what the graph already knows about this work "
        "with --quipu-node <name> (repeatable; find one with quipu_search or "
        "the `quipu` skill), or state why there is none with "
        "--no-graph-context '<reason>'. Nothing is auto-minted: an exact "
        "existing node or an honest reason, never a guess.")


def unstated() -> GraphContext:
    """The advise-mode context: neither a node nor a reason, recorded as such.

    Deliberately NOT an empty exemption string. Silence must not read as an
    excuse — a row that says `missing` can be counted against the fleet, and a
    row carrying a blank reason looks exactly like a considered one.
    """
    return GraphContext(verification=MISSING)


def verify(ctx: GraphContext, registry=None) -> GraphContext:
    """Best effort: does every named node exist? Never raises on an outage.

    Raises GraphNodeUnknown only when the graph ANSWERED and the node was not
    in it — a positive absence, not a silence.
    """
    if not ctx.nodes or registry is None:
        return ctx
    try:
        present = _present(registry, ctx.nodes)
    except Exception as e:  # noqa: BLE001 — every failure here is the same decision
        return replace(ctx, verification=UNVERIFIABLE, detail=str(e)[:200])
    missing = tuple(n for n in ctx.nodes if n not in present)
    if missing:
        raise GraphNodeUnknown(
            f"the graph does not hold: {', '.join(missing)}. Check the exact "
            f"name with quipu_search — node identity here is the literal name "
            f"string, so a near-miss is a different entity. If the concept is "
            f"genuinely not in the graph yet, say so with --no-graph-context "
            f"'<reason>' rather than naming something that is not there.")
    return replace(ctx, verification=VERIFIED)


def _iri_safe(name: str) -> bool:
    """Can this name be pasted into `<onto/name>` without breaking the query?"""
    return bool(name) and not any(c in name for c in ' \t\n<>"{}|^`\\')


def _present(registry, names) -> set:
    """Names that exist, by IRI or by exact rdfs:label.

    Two single-pattern queries rather than one joined query on purpose: a
    conjunction of `?s a ?t` with `rdfs:label` returns zero here for nodes that
    answer both patterns separately (aegis-98gai), and a verification that
    reports present nodes as absent would refuse correct dispatches.
    """
    onto = getattr(registry, "onto", "").rstrip("/#")
    sep = "" if onto.endswith(("/", "#")) else "/"
    # A name carrying a space (or any other character an IRI cannot hold) must
    # NOT be pasted into angle brackets: that is a malformed query, the server
    # rejects the whole batch, and every node in it — including the well-formed
    # ones — comes back unverifiable. Such names are legitimate here (entities
    # are often labelled "dolt server"), so they skip straight to the label
    # probe, which takes them as quoted literals.
    iris = {f"{onto}{sep}{n}": n for n in names if _iri_safe(n)}
    found = set()
    if iris:
        values = " ".join(f"<{iri}>" for iri in iris)
        rows = registry._query(
            f"SELECT DISTINCT ?s WHERE {{ VALUES ?s {{ {values} }} ?s ?p ?o }}")
        for row in rows:
            name = iris.get(str(row.get("s", "")))
            if name:
                found.add(name)
    remaining = [n for n in names if n not in found]
    if remaining:
        literals = " ".join(json.dumps(n) for n in remaining)
        rows = registry._query(
            "SELECT DISTINCT ?l WHERE { VALUES ?l { " + literals + " } "
            "?s <http://www.w3.org/2000/01/rdf-schema#label> ?l }")
        for row in rows:
            label = str(row.get("l", ""))
            if label in remaining:
                found.add(label)
    return found


def record(root, event: str, agent: str, item: str, ctx: GraphContext,
           dry_run: bool = False, session: str = "") -> str | None:
    """Append one adoption row. Returns the ledger path, or None if unwritable.

    A ledger write must never take a dispatch down with it: the work reaching
    the agent matters more than the measurement of it, and a measurement that
    can break the thing it measures gets switched off.
    """
    if not root:
        return None
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        # An epoch alongside the human stamp, because a window is a subtraction
        # and comparing offset-carrying timestamps as STRINGS quietly gets the
        # answer wrong across a timezone change.
        "epoch": int(time.time()),
        "event": event,
        "agent": agent,
        "item": item,
        # The nearest thing to a session id st actually holds at dispatch time
        # is the PANE the work was typed into. Deliberately not an env var: a
        # fabricated `SHANTY_SESSION` would be empty on every real invocation and
        # the column would read as "no session" forever.
        "session": session,
        "actor": os.environ.get("SHANTY_AGENT", ""),
        "nodes": list(ctx.nodes),
        "exemption": ctx.exemption,
        "verification": ctx.verification,
        "dry_run": bool(dry_run),
    }
    try:
        logdir = Path(root) / "logs"
        logdir.mkdir(parents=True, exist_ok=True)
        path = logdir / LEDGER
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        return str(path)
    except OSError:
        return None


def read_rows(root, since_epoch: float = 0.0) -> list[dict]:
    """Ledger rows, oldest first. Malformed lines are SKIPPED, not fatal.

    A row with no `epoch` (written before that field existed) is KEPT rather
    than dropped: excluding it would shrink the denominator silently, which is
    the one direction a coverage number must never be wrong in.
    """
    path = Path(root) / "logs" / LEDGER
    rows = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if since_epoch and float(row.get("epoch") or 0) < since_epoch:
            if row.get("epoch"):
                continue
        rows.append(row)
    return rows


@dataclass
class Summary:
    eligible: int = 0
    with_nodes: int = 0
    exempt: int = 0
    missing: int = 0
    verified: int = 0
    unverifiable: int = 0
    by_agent: dict = field(default_factory=dict)
    reasons: Counter = field(default_factory=Counter)
    nodes: Counter = field(default_factory=Counter)

    @property
    def coverage(self) -> float | None:
        """Share of eligible dispatches carrying a node OR a stated reason.

        None when nothing is eligible — a coverage of "100%" over zero events is
        the flattering answer, and this fleet has paid for those.
        """
        if not self.eligible:
            return None
        return (self.with_nodes + self.exempt) / self.eligible

    @property
    def node_share(self) -> float | None:
        """Share carrying an actual node. The exemption is honest, not a pass."""
        if not self.eligible:
            return None
        return self.with_nodes / self.eligible

    @property
    def zero_node_agents(self) -> list:
        return sorted(a for a, c in self.by_agent.items() if not c.get("nodes"))


def summarize(rows, include_dry_run: bool = False) -> Summary:
    s = Summary()
    for row in rows:
        if row.get("dry_run") and not include_dry_run:
            continue
        s.eligible += 1
        agent = str(row.get("agent") or "-")
        bucket = s.by_agent.setdefault(
            agent, {"total": 0, "nodes": 0, "exempt": 0, "missing": 0})
        bucket["total"] += 1
        names = [n for n in (row.get("nodes") or []) if n]
        if names:
            s.with_nodes += 1
            bucket["nodes"] += 1
            s.nodes.update(names)
        elif row.get("exemption"):
            s.exempt += 1
            bucket["exempt"] += 1
            s.reasons[str(row["exemption"])[:80]] += 1
        else:
            s.missing += 1
            bucket["missing"] = bucket.get("missing", 0) + 1
        if row.get("verification") == VERIFIED:
            s.verified += 1
        elif row.get("verification") == UNVERIFIABLE:
            s.unverifiable += 1
    return s
