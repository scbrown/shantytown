"""Exact native fields for read-only work-item display, never mutation reads.

The SDK show includes comments and reverse dependencies. WorkItem discards
those, so hydrating them amplifies every known-ID display. Keep the fields
and outgoing dependency classification in one bounded observation instead.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import quote, unquote

from .beads import _is_placeholder_dep
from .protocols import WorkItem, blocker_kind, rank_order
from .seeds_plate import SCHEMA, SEEDS, _iri, _query

TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
BLOCKS = "https://scbrown.github.io/quechua/ns#blockedOn"
EDGES = (BLOCKS, SCHEMA + "isPartOf", "http://purl.org/dc/terms/relation",
         "http://www.w3.org/ns/prov#wasDerivedFrom")
FIELDS = (TYPE, SCHEMA + "identifier", SCHEMA + "name", LABEL,
          SCHEMA + "agent", SCHEMA + "dateModified", SCHEMA + "keywords",
          SEEDS + "ontology/status", SEEDS + "ontology/priority",
          SCHEMA + "description", SEEDS + "ontology/notes",
          SCHEMA + "scheduledTime", SCHEMA + "result",
          SCHEMA + "dateCreated", "http://www.w3.org/2002/12/cal/ical#due",
          SEEDS + "ontology/rank", *EDGES)
TARGET_FIELDS = (TYPE, SCHEMA + "identifier", SCHEMA + "name", LABEL,
                 SEEDS + "ontology/status")
CAP = 10000
XSD = "http://www.w3.org/2001/XMLSchema#"


def _time(value):
    # The SDK uses XSD spelling, not UTC conversion: preserve nonzero
    # offsets and trim fractional zeros without discarding nanoseconds.
    dated = re.fullmatch(r"(\d{4}-\d\d-\d\d)(Z|[+-]\d\d:\d\d)", value)
    if dated:
        try:
            datetime.fromisoformat(dated[1])
        except ValueError:
            return value
        return dated[1] + ("Z" if dated[2] in ("+00:00", "-00:00") else dated[2])
    matched = re.fullmatch(
        r"(\d{4}-\d\d-\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)?",
        value,
    )
    if not matched:
        return value
    date, hour, minute, second, fraction, zone = matched.groups()
    if date.startswith("0000"):
        raise ValueError("item time year unsupported")
    if zone and zone != "Z" and (zone[1:3] > "14" or (zone[1:3] == "14" and zone[4:] != "00")):
        return value
    if hour == "24" and minute == second == "00" and not (fraction or "").strip("0"):
        try:
            date = (datetime.fromisoformat(date) + timedelta(days=1)).date().isoformat()
            hour = "00"
        except ValueError:
            return value
    try:
        datetime.fromisoformat(f"{date}T{hour}:{minute}:{second}{zone or ''}")
    except ValueError:
        return value
    fraction = (fraction or "").rstrip("0")
    if zone in ("+00:00", "-00:00"):
        zone = "Z"
    return f"{date}T{hour}:{minute}:{second}" + ("." + fraction if fraction else "") + (zone or "")


#: The ?kind a row carries when the store could not report the literal's type.
UNREPORTED = "unreported"


def _value(row):
    predicate, value, kind = row["p"], row["o"], row["kind"]
    if predicate not in FIELDS:
        raise ValueError("unexpected item predicate")
    if kind == UNREPORTED:
        raise ValueError(f"item field {predicate} has a type the store did not report")
    if predicate == TYPE or predicate in EDGES:
        if kind != "iri":
            raise ValueError("item reference is not an IRI")
    elif predicate == SEEDS + "ontology/priority":
        if kind != XSD + "integer" or not re.fullmatch(r"[+-]?\d+", value):
            raise ValueError("item priority type unreadable")
        value = str(int(value))
    elif predicate == SCHEMA + "agent" and kind == "iri":
        prefix = SEEDS + "principal/"
        if not value.startswith(prefix):
            raise ValueError("item principal unsupported")
        value = unquote(value[len(prefix):])
    elif predicate == SEEDS + "ontology/rank":
        if kind != XSD + "string":
            raise ValueError("item rank type unsupported")
        rank_order(value)
    elif predicate in (SCHEMA + "dateModified", SCHEMA + "scheduledTime",
                       SCHEMA + "dateCreated", "http://www.w3.org/2002/12/cal/ical#due"):
        if kind not in (XSD + "string", XSD + "dateTime", XSD + "date"):
            raise ValueError("item time type unsupported")
        value = _time(value)
    elif kind != XSD + "string":
        raise ValueError("item text type unsupported")
    return value


def _one(facts, predicate, default=None):
    values = facts.get(predicate, set())
    if len(values) > 1:
        raise ValueError("item field has contradictory values")
    return next(iter(values), default)


def _name(facts):
    return _one(facts, SCHEMA + "name", _one(facts, LABEL, ""))


ROW_KEYS = frozenset({"graph", "s", "p", "o", "kind"})


def _subject_facts(rows, graphs, subject):
    found = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            # Name the field: refusing a whole item anonymously sent the reader
            # hunting (aegis-bug), and the predicate is the one fact it needs.
            if not isinstance(row, dict):
                raise ValueError("item field row unreadable: not an object")
            problems = [f"lacks {k}" for k in sorted(ROW_KEYS - set(row))]
            problems += [f"extra {k}" for k in sorted(set(row) - ROW_KEYS)]
            raise ValueError(f"item field row unreadable: {row.get('p', '?')} "
                             + ", ".join(problems))
        if row["graph"] not in graphs or not all(isinstance(v, str) for v in row.values()):
            raise ValueError("item field scope unreadable")
        if row["s"] == subject:
            facts = found.setdefault(row["graph"], {})
            facts.setdefault(row["p"], set()).add(_value(row))
    if len(found) > 1:
        raise ValueError("item appears in both project graphs")
    return next(iter(found.values()), {})


def _target_id(subject):
    prefix = SEEDS + "item/"
    if not subject.startswith(prefix):
        raise ValueError("unsupported dependency subject")
    identifier = unquote(subject[len(prefix):])
    if not identifier or prefix + quote(identifier, safe="") != subject:
        raise ValueError("dependency identity unreadable")
    return identifier


def _fields_query(graphs, subject):
    source_fields = " || ".join(f"?p = {_iri(p)}" for p in FIELDS)
    target_fields = " || ".join(f"?p = {_iri(p)}" for p in TARGET_FIELDS)
    edges = " || ".join(f"?edge = {_iri(p)}" for p in EDGES)
    branches = []
    for graph in graphs:
        branches.append(
            f"{{ GRAPH {_iri(graph)} {{ {_iri(subject)} ?p ?value . "
            f"FILTER({source_fields}) }} BIND({_iri(subject)} AS ?s) "
            f"BIND({_iri(graph)} AS ?graph) }}"
        )
        for target_graph in graphs:
            branches.append(
                f"{{ GRAPH {_iri(graph)} {{ {_iri(subject)} ?edge ?s . FILTER({edges}) }} "
                f"GRAPH {_iri(target_graph)} {{ ?s ?p ?value . FILTER({target_fields}) }} "
                f"BIND({_iri(target_graph)} AS ?graph) }}"
            )
    # Verbose JSON emits native integers and structured dates. Project lexical
    # strings and independent RDF kinds so a string
    # priority or literal dependency cannot impersonate an integer or IRI.
    # DATATYPE() is UNBOUND on the deployed Quipu for an xsd:date literal
    # (measured 2026-10-14 on aegis-l42's date-only scheduledTime, while
    # xsd:dateTime reports normally), and an unbound ?kind dropped the key from
    # the row. The date type is then PROVEN with sameTerm/STRDT, never guessed
    # from the lexical form, and anything still unreported says so explicitly.
    date = _iri(XSD + "date")
    kind = (f'IF(isIRI(?value), "iri", COALESCE(STR(DATATYPE(?value)), '
            f'IF(sameTerm(?value, STRDT(STR(?value), {date})), "{XSD}date", "{UNREPORTED}"), '
            f'"{UNREPORTED}"))')
    return ('SELECT ?graph ?s ?p (STR(?value) AS ?o) '
            f'({kind} AS ?kind) WHERE {{ '
            + " UNION ".join(branches) + f" }} LIMIT {CAP}")


def read_item(tracker, identifier):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identifier):
        raise ValueError("item ID must be an opaque identifier, not a path")
    # A positive old-format observation keeps its SDK adapter. An unreadable
    # native projection never authorizes hydration or an empty result.
    if not tracker._use_remote_projection():
        return tracker.get(identifier)
    proof = tracker.prove_store()
    graphs = (proof.graph, proof.graph + "#seeds-ephemeral")
    subject = SEEDS + "item/" + quote(identifier, safe="")
    # /query's default flat formatter compacts IRI values to CURIEs. Machine
    # identity/predicate readers require the expanded wire representation.
    rows = _query(proof, _fields_query(graphs, subject), tracker.timeout,
                  verbose=True)["rows"]
    if len(rows) >= CAP:
        raise ValueError("item field row ceiling reached")
    facts = _subject_facts(rows, graphs, subject)
    if (facts.get(TYPE) != {SCHEMA + "Action"}
            or _one(facts, SCHEMA + "identifier") != identifier):
        raise LookupError("exact native item absent or identity unsupported")
    status = _one(facts, SEEDS + "ontology/status")
    if status not in ("open", "hooked", "in_progress", "blocked", "deferred", "closed", "tombstone"):
        raise ValueError("item status unreadable")
    priority = _one(facts, SEEDS + "ontology/priority")
    if priority not in ("0", "1", "2", "3", "4"):
        raise ValueError("item priority unreadable")
    dependencies = []
    for edge in EDGES:
        for target in sorted(facts.get(edge, set())):
            target_id = _target_id(target)
            other = _subject_facts(rows, graphs, target)
            if other and (other.get(TYPE) != {SCHEMA + "Action"}
                          or _one(other, SCHEMA + "identifier") != target_id):
                raise ValueError("dependency identity unsupported")
            dependencies.append({"id": target_id, "title": _name(other),
                                 "status": _one(other, SEEDS + "ontology/status"),
                                 "dependency_type": "blocks" if edge == BLOCKS else "context"})
    assignee = _one(facts, SCHEMA + "agent")
    return WorkItem(
        id=identifier, title=_name(facts), status=status, assignee=assignee,
        priority=int(priority), description=_one(facts, SCHEMA + "description", ""),
        notes=_one(facts, SEEDS + "ontology/notes") or None,
        defer_until=_one(facts, SCHEMA + "scheduledTime") or None,
        close_reason=_one(facts, SCHEMA + "result"),
        updated_at=_one(facts, SCHEMA + "dateModified", ""),
        blocker_kind=blocker_kind(sorted(facts.get(SCHEMA + "keywords", set()))),
        open_blockers=tuple(d["id"] for d in dependencies
                            if d["dependency_type"] == "blocks" and d["status"] != "closed"
                            and not _is_placeholder_dep(d)),
        unreadable_deps=sum(_is_placeholder_dep(d) for d in dependencies),
        rank=_one(facts, SEEDS + "ontology/rank"),
        due_at=_one(facts, "http://www.w3.org/2002/12/cal/ical#due"),
        created_at=_one(facts, SCHEMA + "dateCreated"),
    )
