"""Read-only schema.org Seeds projections for plates and crew advice.

Never fetch retained import blobs or descriptions. Unsupported/malformed wire
shapes are UNKNOWN; a failed projection must not trigger full-board hydration.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
import re
import time
from urllib.parse import quote, urlsplit
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .answer import Answer
from .query_identity import read_headers, declared_read_operation

SCHEMA = "https://schema.org/"
SEEDS = "https://seeds.local/"
MESSAGE_PATTERN = json.dumps(r"^\s*(inbox:|mail:)")
FIELDS = {
    SCHEMA + "identifier": "id",
    SCHEMA + "name": "title",
    SCHEMA + "agent": "assignee",
    SCHEMA + "dateModified": "updated_at",
    SCHEMA + "dateCreated": "created_at",
    SCHEMA + "keywords": "labels",
    SEEDS + "ontology/status": "status",
    SEEDS + "ontology/priority": "priority",
    SEEDS + "ontology/issueType": "issue_type",
    SCHEMA + "scheduledTime": "defer_until",
    SEEDS + "ontology/deferUntil": "defer_until",
    "http://www.w3.org/2002/12/cal/ical#due": "due_at",
}


def _iri(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or any(c in value for c in '<>"{}\\\r\n\t '):
        raise ValueError("invalid graph IRI")
    return "<" + value + ">"


def _query(proof, query: str, timeout: int, *, verbose: bool = False) -> dict:
    headers = {"Content-Type": "application/json", "X-Quipu-Client": "seeds"}
    headers.update(read_headers())
    payload = {"query": query}
    if verbose:
        payload["verbose"] = True
    request = Request(
        proof.quipu_url.rstrip("/") + "/query",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError("plate query did not succeed")
        body = response.read(8 * 1024 * 1024 + 1)
        if len(body) > 8 * 1024 * 1024:
            raise ValueError("plate response byte budget reached")
        result = json.loads(body)
    if not isinstance(result, dict):
        raise TypeError("invalid plate response")
    if query.lstrip().startswith("ASK"):
        if type(result.get("result")) is not bool:
            raise ValueError("schema observation unreadable")
        return result
    rows = result.get("rows")
    if (
        result.get("truncated") is not False
        or not isinstance(rows, list)
        or type(result.get("count")) is not int
        or result["count"] != len(rows)
    ):
        raise ValueError("plate query did not prove completeness")
    return result


@declared_read_operation("uses-native-schema")
def uses_native_schema(proof, *, timeout: int = 30) -> bool:
    """A positive legacy revision marker retains the SDK adapter.

    Do not read an older assignedTo/label ledger as an empty schema.org board.
    Unreadable format checks raise; they never authorize a hydration fallback.
    """
    branches = [f"{{ GRAPH {_iri(graph)} {{ ?s <{SEEDS}ontology/revision> ?revision }} }}"
                for graph in (proof.graph, proof.graph + "#seeds-ephemeral")]
    return not _query(proof, "ASK { " + " UNION ".join(branches) + " }", timeout)["result"]


@declared_read_operation("closed-status")
def closed_status(proof, identifier: str, *, timeout: int = 30) -> Answer[bool]:
    """Fresh exact status for a closed: condition, without dependency hydration."""
    how = "Seeds exact condition status; shared and ephemeral graphs"
    try:
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("empty condition identifier")
        subject = _iri(SEEDS + "item/" + quote(identifier, safe=""))
        graphs = (proof.graph, proof.graph + "#seeds-ephemeral")
        branches = [
            f"{{ GRAPH {_iri(graph)} {{ {subject} a <{SCHEMA}Action> . "
            f"OPTIONAL {{ {subject} <{SCHEMA}identifier> ?id }} "
            f"OPTIONAL {{ {subject} <{SEEDS}ontology/status> ?status }} }} "
            f"BIND({json.dumps(graph)} AS ?graph) }}"
            for graph in graphs
        ]
        rows = _query(proof, "SELECT ?graph ?id ?status WHERE { "
                      + " UNION ".join(branches) + " } LIMIT 10", timeout)["rows"]
        # No match, a shadow, or contradictory values are UNKNOWN, not unmet.
        if len(rows) != 1 or rows[0].get("graph") not in graphs:
            raise ValueError("condition status absent or ambiguous")
        row = rows[0]
        if row.get("id") != identifier or row.get("status") not in (
                "open", "hooked", "in_progress", "blocked", "deferred", "closed"):
            raise ValueError("condition identity or status unsupported")
        return Answer.complete_read(row["status"] == "closed", how=how)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        return Answer.capped(False, how=how, caveat=f"condition status unreadable: {error}")


@declared_read_operation("closed-statuses")
def closed_statuses(proof, identifiers, *, timeout: int = 30) -> dict[str, bool | None]:
    """Bounded candidate-only conditions; never a pre-mutation freshness check.

    Each target retains the single-reader's exact identity, shadow and status
    rules. A failed batch remains UNKNOWN without serial hydration retries.
    """
    targets = list(dict.fromkeys(identifiers))
    verdicts = dict.fromkeys(targets)
    graphs = (proof.graph, proof.graph + "#seeds-ephemeral")
    for offset in range(0, len(targets), 25):
        batch = targets[offset:offset + 25]
        try:
            branches = []
            for identifier in batch:
                if not isinstance(identifier, str) or not identifier.strip():
                    raise ValueError("empty condition identifier")
                subject = _iri(SEEDS + "item/" + quote(identifier, safe=""))
                for graph in graphs:
                    branches.append(
                        f"{{ GRAPH {_iri(graph)} {{ {subject} a <{SCHEMA}Action> . "
                        f"OPTIONAL {{ {subject} <{SCHEMA}identifier> ?id }} "
                        f"OPTIONAL {{ {subject} <{SEEDS}ontology/status> ?status }} }} "
                        f"BIND({json.dumps(identifier)} AS ?requested) "
                        f"BIND({json.dumps(graph)} AS ?graph) }}")
            rows = _query(proof, "SELECT ?requested ?graph ?id ?status WHERE { "
                          + " UNION ".join(branches) + " } LIMIT 1000", timeout)["rows"]
            if len(rows) >= 1000:
                raise ValueError("condition batch row budget reached")
            grouped = {identifier: [] for identifier in batch}
            for row in rows:
                if row.get("requested") not in grouped or row.get("graph") not in graphs:
                    raise ValueError("unexpected condition batch identity")
                grouped[row["requested"]].append(row)
            for identifier, found in grouped.items():
                if len(found) != 1:
                    continue
                row = found[0]
                if row.get("id") == identifier and row.get("status") in (
                        "open", "hooked", "in_progress", "blocked", "deferred", "closed"):
                    verdicts[identifier] = row["status"] == "closed"
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass  # This batch stays UNKNOWN; the other batches remain independent.
    return verdicts


def _feed_owner_clause(owners: tuple[str, ...]) -> str:
    # queue_state/hauls accepts any crew-path spelling ending in this worker.
    # Principal IRIs encode path slashes, so match both wire representations.
    names = tuple(dict.fromkeys(a.split("/")[-1] for a in owners))
    alternatives = "|".join(re.escape(n) for name in names
                            for n in dict.fromkeys((name, quote(name, safe=""))))
    if not alternatives:
        return "FILTER(false) "
    pattern = json.dumps(r"(^|/|%2F)(" + alternatives + r")$")
    return f"?s <{SCHEMA}agent> ?owner . FILTER(REGEX(STR(?owner), {pattern})) "


@declared_read_operation("owner-shape-supported")
def owner_shape_supported(proof, agent: str | tuple[str, ...], *, timeout: int = 30) -> bool:
    if isinstance(agent, tuple):
        clauses = [_feed_owner_clause(agent)] if agent else []
    else:
        clauses = [f"?s <{SCHEMA}agent> {term} . "
                   for owner in dict.fromkeys((agent, agent.split("/")[-1]))
                   for term in (json.dumps(owner),
                                _iri(SEEDS + "principal/" + quote(owner, safe="")))]
    if not clauses:
        return True
    known = '("open", "hooked", "in_progress", "blocked", "deferred", "closed", "tombstone")'
    branches = []
    for graph in (proof.graph, proof.graph + "#seeds-ephemeral"):
        for clause in clauses:
            # Restrict the candidate relation before joining owners and types.
            # Missing status and unknown-only status retain the original refusal.
            for candidate, type_clause in (
                (f"?s <{SEEDS}ontology/status> ?bad . FILTER(?bad NOT IN {known}) "
                 f"FILTER NOT EXISTS {{ ?s <{SEEDS}ontology/status> ?good . FILTER(?good IN {known}) }} ",
                 f"?s a <{SCHEMA}Action> . "),
                # A plain-value guard admits the server's indexed anti-join.
                # Non-plain-only statuses are already refused by the other
                # branch; a known plain status still permits mixed values.
                (f"?s a <{SCHEMA}Action> . "
                 f"FILTER NOT EXISTS {{ ?s <{SEEDS}ontology/status> ?any . "
                 f"FILTER(isLiteral(?any) && sameTerm(?any, STR(?any))) }} ",
                 f"?s a <{SCHEMA}Action> . "),
            ):
                branches.append(f"{{ GRAPH {_iri(graph)} {{ {{ {candidate} }} "
                                f"{clause} {type_clause} }} }}")
    return not _query(proof, "ASK { " + " UNION ".join(branches) + " }", timeout)["result"]


class RenderShapeReads:
    """Positive shape observations scoped to one render and one proven store."""

    def __init__(self):
        self.supported = []

    def check(self, proof, scope, *, timeout):
        names = frozenset(a.split("/")[-1] for a in scope) if isinstance(scope, tuple) else None
        for seen_proof, seen_names in self.supported:
            requested = names if names is not None else frozenset((scope.split("/")[-1],))
            if seen_proof == proof and requested <= seen_names:
                return True
        result = owner_shape_supported(proof, scope, timeout=timeout)
        # Only the suffix-matching roster scope can prove later narrower reads.
        # A refusal or exception must never become a cached positive result.
        if result and names is not None:
            self.supported.append((proof, names))
        return result


@declared_read_operation("owner-rows")
def owner_rows(proof, agent: str | None, *, timeout: int = 30,
               feed_owners: tuple[str, ...] | None = None,
               active_only: bool = False, capacity: bool = False,
               include_deferred: bool = False,
               plate_owners: tuple[str, ...] | None = None,
               include_messages: bool = False, shape_reads=None,
               decision: bool = False, messages_only: bool = False,
               include_closed: bool = False) -> Answer[list[dict]]:
    how = (
        "Seeds owner-scoped plate fields"
        if agent is not None
        else "Seeds ready/active priority fields"
    ) + "; shared and ephemeral graphs"
    if feed_owners is not None:
        how = "Seeds Rule Zero idle-owner/unassigned fields; shared and ephemeral graphs"
    if include_deferred:
        how = "Seeds owner-scoped waiting-work metadata; shared and ephemeral graphs"
    try:
        if (include_closed and not messages_only) or (messages_only and (
                agent is None or feed_owners is not None or plate_owners is not None
                or active_only or capacity or decision)):
            raise ValueError("ambiguous inbox projection scope")
        if active_only and feed_owners is None:
            raise ValueError("active projection requires an owner scope")
        if plate_owners is not None:
            if feed_owners is not None or agent is not None or active_only:
                raise ValueError("ambiguous roster scope")
            feed_owners = plate_owners
        scope = feed_owners if feed_owners is not None else agent
        if include_deferred and scope is None:
            raise ValueError("waiting-work metadata requires an owner scope")
        shape_check = shape_reads.check if shape_reads is not None else owner_shape_supported
        if scope is not None and not shape_check(proof, scope, timeout=timeout):
            raise ValueError("owner has an unsupported or absent status")
        graph = proof.graph
        graphs = (graph, graph + "#seeds-ephemeral")
        owners = (
            tuple(dict.fromkeys((agent, agent.split("/")[-1])))
            if agent is not None
            else ()
        )
        if feed_owners is not None:
            owners = tuple(dict.fromkeys(owner for a in feed_owners
                                         for owner in (a, a.split("/")[-1])))
        branches = []
        for g in graphs:
            statuses = (
                ("open", "hooked", "in_progress", "blocked", "deferred")
                + (("closed",) if include_closed else ()) if messages_only else
                ("hooked", "in_progress", "open", "blocked")
                if decision or (plate_owners is not None and capacity) else
                ("hooked", "in_progress", "open") if plate_owners is not None else
                ("in_progress",) if active_only else
                ("open", "in_progress")
                if agent is None
                else ("hooked", "in_progress", "open", "blocked") if capacity
                else ("hooked", "in_progress", "open")
            )
            if include_deferred:
                statuses = (*statuses, "deferred")
            terms = (
                [None]
                if agent is None and feed_owners is None
                else [
                    term
                    for owner in owners
                    for term in (
                        json.dumps(owner),
                        _iri(SEEDS + "principal/" + quote(owner, safe="")),
                    )
                ]
            )
            if feed_owners is not None and not active_only and plate_owners is None:
                # Only unassigned open candidates and the candidate workers'
                # own ready/active hauls can affect Rule Zero.
                branches.append(
                    f'{{ GRAPH {_iri(g)} {{ ?s a <{SCHEMA}Action> ; '
                    f'<{SEEDS}ontology/status> "open" . '
                    f'FILTER NOT EXISTS {{ ?s <{SCHEMA}agent> ?owner . '
                    'FILTER(STR(?owner) != "") } '
                    f'FILTER NOT EXISTS {{ ?s <{SCHEMA}name> ?title . '
                    f'FILTER(REGEX(STR(?title), {MESSAGE_PATTERN})) }} }} '
                    f'BIND({json.dumps(g)} AS ?graph) }}'
                )
            if feed_owners is not None:
                terms = [None] if feed_owners else []
            for term in terms:
                owner_clause = (_feed_owner_clause(feed_owners)
                                if feed_owners is not None
                                else (f"?s <{SCHEMA}agent> {term} . " if term else ""))
                # Keep owner terms constant for indexed selection. Collapse only
                # this exact-owner path; roster/feed projections retain their
                # separately validated query shapes.
                combine_statuses = agent is not None and feed_owners is None
                status_terms = (
                    ("?wanted_status",) if combine_statuses
                    else tuple(json.dumps(status) for status in statuses)
                )
                status_filter = (
                    "FILTER(?wanted_status IN ("
                    + ", ".join(json.dumps(status) for status in statuses)
                    + ")) " if combine_statuses else ""
                )
                for status_term in status_terms:
                    branches.append(
                        f"{{ GRAPH {_iri(g)} {{ {owner_clause}?s a <{SCHEMA}Action> ; "
                        f"<{SEEDS}ontology/status> {status_term} . "
                        + status_filter + (f"?s <{SCHEMA}name> ?title . "
                           f"FILTER(REGEX(STR(?title), {MESSAGE_PATTERN})) "
                           if messages_only else "" if include_messages else
                         f"FILTER NOT EXISTS {{ ?s <{SCHEMA}name> ?title . "
                         f"FILTER(REGEX(STR(?title), {MESSAGE_PATTERN})) }} ") + "} "
                        f"BIND({json.dumps(g)} AS ?graph) }}"
                    )
        query = (
            "SELECT (STR(?s) AS ?subject) ?graph WHERE { "
            + " UNION ".join(branches)
            + " } LIMIT 10000"
        )
        candidates = _query(proof, query, timeout)["rows"]
        if len(candidates) >= 10000:
            raise ValueError("plate candidate budget reached")
        selected = {}
        for candidate in candidates:
            subject, graph = candidate.get("subject"), candidate.get("graph")
            if (
                not isinstance(subject, str)
                or not subject.startswith(SEEDS + "item/")
                or graph not in graphs
            ):
                raise ValueError("invalid plate candidate")
            if subject in selected and selected[subject] != graph:
                raise ValueError("seed appears in shared and ephemeral graphs")
            selected[subject] = graph
        wire = []
        pairs = list(selected.items())
        # Discover scoped IDs first, then bind every subject and predicate.
        # This follows the SDK's indexed query shapes without hydration.
        for offset in range(0, len(pairs), 500):
            chunk = pairs[offset : offset + 500]
            # Keep graph and predicate constant in each indexed branch.
            # A variable GRAPH/predicate pattern can scan the whole store
            # before joining VALUES on deployed engines.
            branches = []
            for g in graphs:
                bindings = " ".join(_iri(subject) for subject, graph in chunk if graph == g)
                if not bindings:
                    continue
                for predicate in FIELDS:
                    branches.append(
                        f"{{ VALUES ?s {{ {bindings} }} GRAPH {_iri(g)} "
                        f"{{ ?s {_iri(predicate)} ?value }} "
                        f"BIND({json.dumps(g)} AS ?graph) "
                        f"BIND({json.dumps(predicate)} AS ?predicate) }}"
                    )
            query = (
                "SELECT (STR(?s) AS ?subject) ?graph ?predicate ?value WHERE { "
                + " UNION ".join(branches) + " } LIMIT 10000"
            )
            part = _query(proof, query, timeout)["rows"]
            if len(part) >= 10000 or len(wire) + len(part) >= 10000:
                raise ValueError("plate field budget reached")
            wire.extend(part)
        grouped = {}
        subjects = {}
        for field in wire:
            subject, g, p, value = (
                field.get(k) for k in ("subject", "graph", "predicate", "value")
            )
            if (
                not isinstance(subject, str)
                or not subject.startswith(SEEDS + "item/")
                or g not in graphs
                or selected.get(subject) != g
                or p not in FIELDS
            ):
                raise ValueError("invalid plate field")
            if subject in subjects and subjects[subject] != g:
                raise ValueError("seed appears in shared and ephemeral graphs")
            subjects[subject] = g
            row = grouped.setdefault(subject, {"labels": []})
            key = FIELDS[p]
            if isinstance(value, dict):
                if set(value) != {"datatype", "value"} or key not in (
                    "updated_at",
                    "created_at",
                    "defer_until",
                    "due_at",
                ):
                    raise ValueError("unsupported typed plate value")
                value = value["value"]
            if (
                key == "assignee"
                and isinstance(value, str)
                and value.startswith(SEEDS + "principal/")
            ):
                from urllib.parse import unquote

                value = unquote(value[len(SEEDS + "principal/") :])
            if key == "labels":
                if not isinstance(value, str):
                    raise ValueError("invalid label")
                if value not in row[key]:
                    row[key].append(value)
            else:
                if key in row and row[key] != value:
                    raise ValueError("ambiguous plate field")
                row[key] = value
        if set(grouped) != set(selected):
            raise ValueError("plate candidate disappeared during projection")
        rows = []
        for subject, row in grouped.items():
            if row.get("status") in ("closed", "tombstone") and not include_closed:
                continue
            if (
                not all(isinstance(row.get(k), str) for k in ("id", "title", "status"))
                or not row["id"].strip()
                or (agent is not None and row.get("assignee") not in owners)
                or (plate_owners is not None and not include_deferred
                    and row.get("assignee") not in owners)
                or (include_deferred and not row.get("assignee"))
                or (feed_owners is not None and row.get("assignee")
                    and row["assignee"].split("/")[-1]
                    not in {a.split("/")[-1] for a in feed_owners})
                or ("assignee" in row and not isinstance(row["assignee"], str))
                or type(row.get("priority")) is not int
                or not 0 <= row["priority"] <= 4
                or (feed_owners is not None and plate_owners is None and not active_only
                    and (not isinstance(row.get("issue_type"), str)
                         or not row["issue_type"]))
                or row["status"]
                not in (
                    "open",
                    "hooked",
                    "in_progress",
                    "blocked",
                    "deferred",
                    "closed",
                    "tombstone",
                )
                or subject != SEEDS + "item/" + quote(row["id"], safe="")
            ):
                raise ValueError("incomplete or unsupported plate candidate")
            if messages_only:
                from .inbox import is_message
                if not is_message(row["title"]) or row["status"] == "tombstone":
                    raise ValueError("unexpected inbox candidate")
            if include_closed or row["status"] not in ("closed", "tombstone"):
                rows.append(row)
        return Answer.complete_read(rows, how=how)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        return Answer.capped([], how=how, caveat=f"owner plate unreadable: {error}")


@declared_read_operation("deferred-rows")
def deferred_rows(proof, *, timeout: int = 30, include_asks=False, target_ids=None) -> Answer[list[dict]]:
    """Project evaluation fields; fresh full detail still gates every release."""
    how = "Seeds deferred and dated-open candidate fields; shared and ephemeral graphs"
    fields = {**FIELDS, SEEDS + "ontology/notes": "notes"}
    try:
        graphs = (proof.graph, proof.graph + "#seeds-ephemeral")
        branches = []
        for graph in graphs:
            # Select by indexed status/date before joining Action type. Repeating
            # the type join in every UNION arm scans the same broad population.
            choices = [f'{{ ?s <{SEEDS}ontology/status> "deferred" }}']
            for predicate in (SCHEMA + "scheduledTime", SEEDS + "ontology/deferUntil"):
                choices.append(
                    f'{{ ?s <{SEEDS}ontology/status> "open" ; {_iri(predicate)} ?date }}')
            if include_asks:
                for status in ("open", "blocked", "in_progress"):
                    choices.append(
                        f'{{ ?s <{SEEDS}ontology/status> {json.dumps(status)} ; '
                        f'<{SEEDS}ontology/notes> ?askNotes . '
                        'FILTER(CONTAINS(?askNotes, "steve_quote:")) }')
                for status in ("open", "blocked"):
                    choices.append(
                        f'{{ ?s <{SEEDS}ontology/status> {json.dumps(status)} ; '
                        f'<{SEEDS}ontology/notes> ?gateNotes . '
                        'FILTER(CONTAINS(?gateNotes, "resume_when:")) }')
            branches.append(
                '{ { SELECT DISTINCT ?s WHERE { GRAPH ' + _iri(graph) + ' { '
                + " UNION ".join(choices) + ' } } } GRAPH ' + _iri(graph)
                + f' {{ ?s a <{SCHEMA}Action> }} '
                + f'BIND({json.dumps(graph)} AS ?graph) }}')
        if target_ids is not None:
            if not target_ids or len(target_ids) > 25:
                raise ValueError("gate target batch must contain 1..25 IDs")
            branches = []
            for identifier in target_ids:
                if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identifier):
                    raise ValueError("invalid gate target ID")
                subject = SEEDS + "item/" + quote(identifier, safe="")
                for graph in graphs:
                    branches.append(f'{{ GRAPH {_iri(graph)} {{ {_iri(subject)} a <{SCHEMA}Action> }} '
                                    f'BIND({_iri(subject)} AS ?s) BIND({json.dumps(graph)} AS ?graph) }}')
        # The unscoped 16-arm UNION exhausted the server execution budget
        # (aegis-9lb). Keep each clause unchanged but execute it separately.
        # Exact-ID gate reads retain their bounded batch. A failed branch must
        # refuse the whole read; never hydrate a partially collected candidate set.
        groups = [branches] if target_ids is not None else [[branch] for branch in branches]
        candidates = []
        deadline = time.monotonic() + timeout
        for group in groups:
            time_left = deadline - time.monotonic()
            if time_left <= 0:
                raise ValueError("deferral candidate time budget reached")
            remaining = 10000 - len(candidates)
            rows = _query(proof, "SELECT (STR(?s) AS ?subject) ?graph WHERE { "
                          + " UNION ".join(group) + f" }} LIMIT {remaining}", time_left)["rows"]
            if time.monotonic() >= deadline:
                raise ValueError("deferral candidate time budget reached")
            candidates.extend(rows)
            if len(candidates) >= 10000:
                raise ValueError("deferral candidate budget reached")
        selected = {}
        for row in candidates:
            subject, graph = row.get("subject"), row.get("graph")
            if (not isinstance(subject, str) or not subject.startswith(SEEDS + "item/")
                    or graph not in graphs):
                raise ValueError("invalid deferral candidate")
            if subject in selected and selected[subject] != graph:
                raise ValueError("deferral candidate shadows another graph")
            selected[subject] = graph
        grouped = {subject: {"labels": [], "notes": None, "assignee": None,
                             "defer_until": None} for subject in selected}
        pairs = list(selected.items())
        total = 0
        for offset in range(0, len(pairs), 25):
            branches = []
            # Read both graphs for every selected subject: even a shadow with
            # a different status must refuse, rather than hide a condition.
            for subject, _ in pairs[offset:offset + 25]:
                for graph in graphs:
                    for predicate in fields:
                        branches.append(
                            f'{{ GRAPH {_iri(graph)} {{ {_iri(subject)} {_iri(predicate)} ?value }} '
                            f'BIND({_iri(subject)} AS ?s) BIND({_iri(predicate)} AS ?p) '
                            f'BIND({json.dumps(graph)} AS ?graph) }}'
                        )
            wire = _query(proof, "SELECT (STR(?s) AS ?subject) ?graph "
                          "(STR(?p) AS ?predicate) ?value WHERE { "
                          + " UNION ".join(branches) + " } LIMIT 10000", timeout)["rows"]
            total += len(wire)
            if len(wire) >= 10000 or total >= 100000:
                raise ValueError("deferral field budget reached")
            expected = dict(pairs[offset:offset + 25])
            seen = set()
            for field in wire:
                subject, graph, predicate, value = (
                    field.get(k) for k in ("subject", "graph", "predicate", "value"))
                if (subject not in expected or graph != expected[subject]
                        or predicate not in fields):
                    raise ValueError("invalid or shadowed deferral field")
                key = fields[predicate]
                if isinstance(value, dict):
                    # due_at is an xsd:dateTime like the plate reader's (aegis-qc2):
                    # without it, one item with a due date made every deferral
                    # read refuse, so the deferral sweep never produced a summary.
                    if set(value) != {"datatype", "value"} or key not in (
                            "created_at", "updated_at", "defer_until", "due_at"):
                        raise ValueError(f"unsupported typed deferral field: {key} "
                                         f"({value.get('datatype')!s:.80})")
                    value = value["value"]
                if key == "assignee" and isinstance(value, str) and value.startswith(SEEDS + "principal/"):
                    from urllib.parse import unquote
                    value = unquote(value[len(SEEDS + "principal/"):])
                row = grouped[subject]
                if key == "labels":
                    if not isinstance(value, str):
                        raise ValueError("invalid deferral label")
                    if value not in row[key]:
                        row[key].append(value)
                else:
                    if (subject, key) in seen and row[key] != value:
                        raise ValueError("ambiguous deferral field")
                    seen.add((subject, key))
                    row[key] = value
        statuses = (("closed", "tombstone", "deferred", "open", "blocked", "in_progress")
                    if target_ids is not None else
                    ("deferred", "open", "blocked", "in_progress") if include_asks else
                    ("deferred", "open"))
        for subject, row in grouped.items():
            if (not all(isinstance(row.get(k), str) for k in ("id", "title", "created_at", "status"))
                    or not row["id"] or subject != SEEDS + "item/" + quote(row["id"], safe="")
                    or type(row.get("priority")) is not int or not 0 <= row["priority"] <= 4
                    or row["status"] not in statuses
                    or (target_ids is None and row["status"] != "deferred" and row["defer_until"] is None
                        and not (include_asks and any(marker in (row["notes"] or "")
                                                     for marker in ("steve_quote:", "resume_when:"))))
                    or any(row[k] is not None and not isinstance(row[k], str)
                           for k in ("notes", "assignee", "defer_until"))):
                raise ValueError("incomplete or changed deferral candidate")
            row["labels"].sort()
        def ranking(row):
            return row["priority"], row["created_at"], row["id"]
        rows = [row for status in statuses
                for row in sorted(grouped.values(), key=ranking) if row["status"] == status]
        return Answer.complete_read(rows, how=how)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        return Answer.capped([], how=how, caveat=f"deferral candidates unreadable: {error}")


@declared_read_operation("unassigned-counts")
def unassigned_counts(proof, *, timeout: int = 30) -> tuple[int, int]:
    """Exact open/unassigned counts without fetching any issue payload."""
    graphs = (proof.graph, proof.graph + "#seeds-ephemeral")
    counts = []
    for top in (False, True):
        branches = []
        for graph in graphs:
            priority = (
                f"?s <{SEEDS}ontology/priority> ?priority . FILTER(?priority <= 1) "
                if top
                else ""
            )
            branches.append(
                f'{{ GRAPH {_iri(graph)} {{ ?s <{SEEDS}ontology/status> "open" ; '
                f"a <{SCHEMA}Action> . FILTER NOT EXISTS {{ ?s <{SCHEMA}agent> ?owner . "
                'FILTER(STR(?owner) != "") } ' + priority + "} }"
            )
        query = (
            "SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { "
            + " UNION ".join(branches)
            + " }"
        )
        rows = _query(proof, query, timeout)["rows"]
        if len(rows) != 1 or type(rows[0].get("n")) is not int or rows[0]["n"] < 0:
            raise ValueError("unassigned count unreadable")
        counts.append(rows[0]["n"])
    if counts[1] > counts[0]:
        raise ValueError("unassigned counts changed during observation")
    return tuple(counts)


@declared_read_operation("unblocked-ids")
def unblocked_ids(proof, rows: list[dict], *, timeout: int = 30) -> set[str]:
    """SDK-native readiness test on already projected IDs, in bounded batches.

    As in sd ready, an absent blocker status is not an open blocker. Only the
    native blocks predicate participates; related/parent links do not gate it.
    """
    result = set()
    graphs = (proof.graph, proof.graph + "#seeds-ephemeral")
    for offset in range(0, len(rows), 250):
        chunk = rows[offset : offset + 250]
        expected = {row["id"] for row in chunk}
        branches = []
        for graph in graphs:
            for state in dict.fromkeys(row["status"] for row in chunk):
                bindings = " ".join(
                    _iri(SEEDS + "item/" + quote(row["id"], safe=""))
                    for row in chunk if row["status"] == state)
                branches.append(
                    f"{{ GRAPH {_iri(graph)} {{ VALUES ?s {{ {bindings} }} "
                    f"?s <{SEEDS}ontology/status> {json.dumps(state)} ; "
                    f"a <{SCHEMA}Action> ; <{SCHEMA}identifier> ?id . "
                    f"FILTER NOT EXISTS {{ ?s <https://scbrown.github.io/quechua/ns#blockedOn> ?blocker . "
                    f"?blocker <{SEEDS}ontology/status> ?bs . "
                    'FILTER(?bs != "closed" && ?bs != "tombstone") } } }')
        query = "SELECT DISTINCT ?id WHERE { " + " UNION ".join(branches) + " } LIMIT 1000"
        wire = _query(proof, query, timeout)["rows"]
        if len(wire) >= 1000 or any(
            not isinstance(r.get("id"), str) or r["id"] not in expected for r in wire
        ):
            raise ValueError("invalid readiness response")
        result.update(r["id"] for r in wire)
    return result


@declared_read_operation("ready-count")
def ready_count(proof, *, timeout: int = 30, now=None) -> int:
    """Count SDK-ready rows with only IDs and deferral dates.

    Unlike work/plate selection, this existing utilization metric includes
    messages. Preserve its semantics while avoiding payload hydration.
    """
    now = datetime.now(timezone.utc) if now is None else now
    branches = []
    graphs = (proof.graph, proof.graph + "#seeds-ephemeral")
    for graph in graphs:
        branches.append(
            f'{{ GRAPH {_iri(graph)} {{ ?s <{SEEDS}ontology/status> "open" ; '
            f'a <{SCHEMA}Action> ; <{SCHEMA}identifier> ?id . '
            f'OPTIONAL {{ ?s <{SCHEMA}scheduledTime> ?due }} '
            f'OPTIONAL {{ ?s <{SEEDS}ontology/deferUntil> ?legacy_due }} '
            'FILTER NOT EXISTS { ?s <https://scbrown.github.io/quechua/ns#blockedOn> ?blocker . '
            f'?blocker <{SEEDS}ontology/status> ?bs . ?s a <{SCHEMA}Action> . '
            'FILTER(?bs != "closed" && ?bs != "tombstone") } } '
            f'BIND({json.dumps(graph)} AS ?graph) }}'
        )
    query = 'SELECT (STR(?s) AS ?subject) ?graph ?id ?due ?legacy_due WHERE { '
    query += ' UNION '.join(branches) + ' } LIMIT 10000'
    rows = _query(proof, query, timeout)["rows"]
    if len(rows) >= 10000:
        raise ValueError("ready count metadata budget reached")
    seen = {}
    for row in rows:
        identifier, graph = row.get("id"), row.get("graph")
        if (not isinstance(identifier, str) or not identifier.strip()
                or graph not in graphs
                or row.get("subject") != SEEDS + "item/" + quote(identifier, safe="")):
            raise ValueError("invalid ready count candidate")
        dates = []
        for field in ("due", "legacy_due"):
            value = row.get(field)
            if isinstance(value, dict):
                if set(value) != {"datatype", "value"}:
                    raise ValueError("invalid deferral date")
                value = value["value"]
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError("invalid deferral date")
                date = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if date.tzinfo is None:
                    raise ValueError("deferral date lacks timezone")
                dates.append(date)
        if len(set(dates)) > 1:
            raise ValueError("ambiguous deferral dates")
        observation = (graph, dates[0] if dates else None)
        if identifier in seen and seen[identifier] != observation:
            raise ValueError("ambiguous ready count candidate")
        seen[identifier] = observation
    return sum(date is None or date <= now for _, date in seen.values())


@declared_read_operation("due-rows")
def due_rows(proof, *, timeout=30) -> Answer[list[dict]]:
    """Only active due-bearing rows and reminder fields, never full hydration."""
    how = "Seeds due-bearing active fields, shared+ephemeral, maximum2000 rows"
    try:
        branches = []
        for graph in (proof.graph, proof.graph + "#seeds-ephemeral"):
            for status in ("open", "in_progress", "blocked"):
                branches.append(
                    f'{{ GRAPH {_iri(graph)} {{ ?s a <{SCHEMA}Action> ; '
                    f'<{SCHEMA}identifier> ?id ; <{SCHEMA}name> ?title ; '
                    f'<{SEEDS}ontology/status> {json.dumps(status)} ; '
                    '<http://www.w3.org/2002/12/cal/ical#due> ?due . '
                    f'OPTIONAL {{ ?s <{SCHEMA}agent> ?assignee }} '
                    f'OPTIONAL {{ ?s <{SCHEMA}keywords> ?label }} }} '
                    f'BIND({json.dumps(status)} AS ?status) '
                    f'BIND({json.dumps(graph)} AS ?graph) }}')
        wire = _query(proof, "SELECT (STR(?s) AS ?subject) ?graph ?id ?title ?status ?due ?assignee ?label WHERE { "
                      + " UNION ".join(branches) + " } LIMIT 2000", timeout)["rows"]
        if len(wire) >= 2000:
            raise ValueError("due candidate budget reached")
        grouped = {}
        for item in wire:
            due = item.get("due")
            if isinstance(due, dict) and set(due) == {"datatype", "value"}:
                due = due["value"]
            if not all(isinstance(item.get(k),str) and item[k] for k in ("graph","id","title","status")) or not isinstance(due,str):
                raise ValueError("incomplete due row")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", item["id"]):
                raise ValueError("invalid due identifier")
            if (item["graph"] not in (proof.graph, proof.graph + "#seeds-ephemeral")
                    or item.get("subject") != SEEDS + "item/" + quote(item["id"], safe="")):
                raise ValueError("invalid due subject or graph")
            assignee = item.get("assignee")
            if isinstance(assignee,str) and assignee.startswith(SEEDS + "principal/"):
                from urllib.parse import unquote
                assignee = unquote(assignee[len(SEEDS + "principal/"):])
            if assignee is not None and not isinstance(assignee,str):
                raise ValueError("invalid due owner")
            row = {"id":item["id"],"title":item["title"],"status":item["status"],
                   "due_at":due,"assignee":assignee,"graph":item["graph"]}
            previous = grouped.setdefault(row["id"], {**row,"labels":[]})
            if any(previous[k] != value for k,value in row.items()):
                raise ValueError("conflicting or shadowed due row")
            if item.get("label") is not None:
                if not isinstance(item["label"],str):
                    raise ValueError("invalid due label")
                previous["labels"].append(item["label"])
        return Answer.complete_read(list(grouped.values()),how=how)
    except Exception as error:
        cause = type(error).__name__
        if isinstance(error, HTTPError) and type(error.code) is int and 400 <= error.code <= 599:
            cause += f":{error.code}"
        return Answer.capped([],how=how,caveat=f"due projection UNKNOWN: {cause}")
