"""Read-only known-item fields: complete evidence, no hydration fallback."""
import json
from argparse import Namespace
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from shantytown import seeds_item, work_item
from shantytown.sd import SdTracker, StoreProof

GRAPH = "https://seeds.local/project/test"
SUBJECT = "https://seeds.local/item/test-one"


def rows(subject=SUBJECT, graph=GRAPH, **fields):
    values = {seeds_item.TYPE: seeds_item.SCHEMA + "Action",
              seeds_item.SCHEMA + "identifier": subject.rsplit("/", 1)[-1],
              seeds_item.SCHEMA + "name": "positive item",
              seeds_item.SEEDS + "ontology/status": "open",
              seeds_item.SEEDS + "ontology/priority": "0"}
    values.update(fields)
    return [{"graph": graph, "s": subject, "p": p, "o": o,
             "kind": ("iri" if p == seeds_item.TYPE or p in seeds_item.EDGES
                      or (p == seeds_item.SCHEMA + "agent" and o.startswith(seeds_item.SEEDS + "principal/"))
                      else seeds_item.XSD + ("integer" if p.endswith("/priority") else "string"))}
            for p, o in values.items()]


def tracker(monkeypatch, payload):
    value = SdTracker(quipu="https://knowledge.example", prefix="test")
    value._proof = StoreProof("--quipu", None, value.quipu, "test", GRAPH)
    value._native_projection = True
    monkeypatch.setattr(value, "prove_store", lambda: value._proof)
    calls = []
    def query(proof, text, timeout, *, verbose=False):
        assert verbose is True
        calls.append(text)
        return {"rows": payload}
    monkeypatch.setattr(seeds_item, "_query", query)
    monkeypatch.setattr(value, "get", lambda _: pytest.fail("native display hydrated SDK show"))
    return value, calls


def test_all_display_fields_and_dependency_classification(monkeypatch):
    source = rows(**{
        seeds_item.SCHEMA + "agent": seeds_item.SEEDS + "principal/crew%2Fwu",
        seeds_item.SCHEMA + "description": "body preserved",
        seeds_item.SEEDS + "ontology/notes": "resume condition",
        seeds_item.SCHEMA + "scheduledTime": "2026-10-13T00:00:00Z",
        seeds_item.SCHEMA + "result": "retained reason",
        seeds_item.SCHEMA + "dateModified": "2026-10-12T00:00:00Z",
        seeds_item.SCHEMA + "keywords": "blocked:external",
    })
    for target in ("open", "closed", "missing", "tombstone"):
        uri = seeds_item.SEEDS + "item/test-" + target
        source.append({"graph": GRAPH, "s": SUBJECT, "p": seeds_item.BLOCKS, "o": uri, "kind": "iri"})
        if target != "missing":
            source.extend(rows(uri, **{seeds_item.SEEDS + "ontology/status": target}))
    value, calls = tracker(monkeypatch, source)
    result = asdict(seeds_item.read_item(value, "test-one"))
    assert result == {
        "id": "test-one", "title": "positive item", "status": "open",
        "assignee": "crew/wu", "priority": 0, "notes": "resume condition",
        "defer_until": "2026-10-13T00:00:00Z", "blocker_kind": "blocked:external",
        "open_blockers": ("test-missing", "test-open", "test-tombstone"),
        "unreadable_deps": 0, "description": "body preserved",
        "updated_at": "2026-10-12T00:00:00Z", "close_reason": "retained reason",
        "rank": None, "due_at": None, "created_at": None,
    }
    assert len(calls) == 1
    assert "LIMIT 10000" in calls[0] and "schema.org/parentItem" not in calls[0]


def test_ephemeral_item_and_cross_graph_blocker(monkeypatch):
    target = seeds_item.SEEDS + "item/test-blocker"
    source = rows(graph=GRAPH + "#seeds-ephemeral", **{seeds_item.BLOCKS: target})
    source += rows(target)
    value, _ = tracker(monkeypatch, source)
    assert seeds_item.read_item(value, "test-one").open_blockers == ("test-blocker",)


@pytest.mark.parametrize("mutation", [
    lambda r: [],
    lambda r: r + rows(graph=GRAPH + "#seeds-ephemeral"),
    lambda r: r + [dict(r[-1], o="4")],
    lambda r: [dict(x, graph="urn:wrong") for x in r],
    lambda r: [dict(x, extra="unproven") for x in r],
    lambda r: [dict(x, o="wrong-id") if x["p"].endswith("identifier") else x for x in r],
    lambda r: [dict(x, o="unknown") if x["p"].endswith("status") else x for x in r],
    lambda r: [x for x in r if not x["p"].endswith("priority")],
    lambda r: [dict(x, o="false") if x["p"].endswith("priority") else x for x in r],
    lambda r: [dict(x, o="urn:foreign-type") if x["p"] == seeds_item.TYPE else x for x in r],
    lambda r: [dict(x, kind=seeds_item.XSD + "string") if x["p"].endswith("priority") else x for x in r],
    lambda r: [dict(x, kind="iri") if x["p"].endswith("name") else x for x in r],
    lambda r: r * 2000,
])
def test_unknown_or_capped_fields_refuse_without_hydration(monkeypatch, mutation):
    value, _ = tracker(monkeypatch, mutation(rows()))
    with pytest.raises((ValueError, LookupError)):
        seeds_item.read_item(value, "test-one")


def test_external_and_missing_markers_stay_unreadable(monkeypatch):
    uri = seeds_item.SEEDS + "item/test-placeholder"
    payload = rows(**{seeds_item.BLOCKS: uri}) + rows(uri, **{
        seeds_item.SEEDS + "ontology/status": "tombstone",
        seeds_item.SCHEMA + "name": "[missing issue: test-placeholder]",
    })
    value, _ = tracker(monkeypatch, payload)
    item = seeds_item.read_item(value, "test-one")
    assert item.open_blockers == () and item.unreadable_deps == 1


def test_legacy_observation_uses_exact_sdk(monkeypatch):
    value = SdTracker(quipu="https://knowledge.example", prefix="test")
    expected = SimpleNamespace(id="test-one")
    monkeypatch.setattr(value, "_use_remote_projection", lambda: False)
    calls = []
    monkeypatch.setattr(value, "get", lambda identifier: calls.append(identifier) or expected)
    assert seeds_item.read_item(value, "test-one") is expected
    assert calls == ["test-one"]


@pytest.mark.parametrize("identifier", ["", "../test", "bad/id", "bad id"])
def test_invalid_id_refuses_before_format_or_transport(monkeypatch, identifier):
    value = SdTracker(quipu="https://knowledge.example", prefix="test")
    monkeypatch.setattr(value, "_use_remote_projection", lambda: pytest.fail("invalid ID reached transport"))
    with pytest.raises(ValueError):
        seeds_item.read_item(value, identifier)


def test_mutation_precondition_and_readback_remain_full_sdk(monkeypatch):
    value = SdTracker(quipu="https://knowledge.example", prefix="test")
    calls = []
    from shantytown.protocols import WorkItem
    current = [WorkItem(id="test-one")]
    monkeypatch.setattr(seeds_item, "read_item", lambda *_: pytest.fail("mutation used display projection"))
    monkeypatch.setattr(value, "get", lambda identifier: calls.append(("get", identifier)) or current[0])
    def update(identifier, **fields):
        calls.append(("update", identifier, fields))
        current[0] = WorkItem(id=identifier, status=fields["status"])
    monkeypatch.setattr(value, "update", update)
    result = work_item.command(Namespace(item="test-one", item_action="update", status="in_progress",
                                        assignee=None, priority=None, description_file=None,
                                        notes_file=None, dry_run=False), value)
    assert result["result"] == "verified"
    assert calls == [("get", "test-one"), ("update", "test-one", {"status": "in_progress"}),
                     ("get", "test-one")]


@pytest.mark.parametrize("response", [
    {"count": 0, "rows": rows(), "truncated": False},
    {"count": 5, "rows": rows(), "truncated": True},
    {"count": 5, "rows": rows()},
    {"count": 0, "rows": [], "truncated": False},
    [],
])
def test_actual_wire_guard_refuses_bad_envelopes_without_sdk(monkeypatch, response):
    from shantytown import seeds_plate
    value = SdTracker(quipu="https://knowledge.example", prefix="test")
    proof = StoreProof("--quipu", None, value.quipu, "test", GRAPH)
    monkeypatch.setattr(value, "prove_store", lambda: proof)
    value._native_projection = True
    monkeypatch.setattr(value, "get", lambda _: pytest.fail("wire failure hydrated SDK"))
    class Wire:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, _): return json.dumps(response).encode()
    monkeypatch.setattr(seeds_plate, "urlopen", lambda *_, **__: Wire())
    with pytest.raises((ValueError, TypeError, LookupError)):
        seeds_item.read_item(value, "test-one")


def test_unproven_format_refuses_without_fallback(monkeypatch):
    from shantytown import seeds_plate
    value = SdTracker(quipu="https://knowledge.example", prefix="test")
    proof = StoreProof("--quipu", None, value.quipu, "test", GRAPH)
    monkeypatch.setattr(value, "prove_store", lambda: proof)
    monkeypatch.setattr(value, "get", lambda _: pytest.fail("unknown schema hydrated SDK"))
    def fail(*_): raise ValueError("format unproven")
    monkeypatch.setattr(seeds_plate, "_query", fail)
    with pytest.raises(RuntimeError, match="format unproven"):
        seeds_item.read_item(value, "test-one")


@pytest.mark.parametrize("value,expected", [
    ("2026-10-12T00:00:00+00:00", "2026-10-12T00:00:00Z"),
    ("2026-10-12T00:00:00.123456789+01:00", "2026-10-12T00:00:00.123456789+01:00"),
    ("2026-10-12T00:00:00.100Z", "2026-10-12T00:00:00.1Z"),
    ("2026-10-12T24:00:00.000+00:00", "2026-10-13T00:00:00Z"),
    ("2026-10-12", "2026-10-12"),
    ("2026-10-12+00:00", "2026-10-12Z"),
    ("not a time", "not a time"),
])
def test_sdk_time_spelling_retains_precision(value, expected):
    assert seeds_item._time(value) == expected


def test_literal_uri_assignee_is_not_decoded_as_a_principal(monkeypatch):
    payload = rows()
    literal = seeds_item.SEEDS + "principal/crew%2Fwu"
    payload.append({"graph": GRAPH, "s": SUBJECT, "p": seeds_item.SCHEMA + "agent",
                    "o": literal, "kind": seeds_item.XSD + "string"})
    value, _ = tracker(monkeypatch, payload)
    assert seeds_item.read_item(value, "test-one").assignee == literal


# aegis-bug: aegis-l42's date-only scheduledTime. The deployed Quipu returns an
# UNBOUND DATATYPE() for an xsd:date literal, which dropped `kind` from the row and
# refused the whole item as "item field row unreadable" with no field named.
SCHEDULED = seeds_item.SCHEMA + "scheduledTime"


def test_date_only_scheduled_time_reads(monkeypatch):
    payload = rows(**{SCHEDULED: "2026-10-14"})
    payload = [dict(x, kind=seeds_item.XSD + "date") if x["p"] == SCHEDULED else x
               for x in payload]
    value, _ = tracker(monkeypatch, payload)
    assert seeds_item.read_item(value, "test-one").defer_until == "2026-10-14"


def test_a_row_without_kind_names_its_predicate(monkeypatch):
    payload = [{k: v for k, v in x.items() if k != "kind"} if x["p"] == SCHEDULED else x
               for x in rows(**{SCHEDULED: "2026-10-14"})]
    value, _ = tracker(monkeypatch, payload)
    with pytest.raises(ValueError, match=f"{SCHEDULED} lacks kind"):
        seeds_item.read_item(value, "test-one")


def test_an_unreported_type_names_its_field_and_is_not_guessed(monkeypatch):
    payload = [dict(x, kind=seeds_item.UNREPORTED) if x["p"] == SCHEDULED else x
               for x in rows(**{SCHEDULED: "2026-10-14"})]
    value, _ = tracker(monkeypatch, payload)
    with pytest.raises(ValueError, match=f"{SCHEDULED} has a type the store did not report"):
        seeds_item.read_item(value, "test-one")


def test_query_proves_the_date_type_instead_of_dropping_kind():
    text = seeds_item._fields_query((GRAPH,), SUBJECT)
    assert f"STRDT(STR(?value), <{seeds_item.XSD}date>)" in text
    assert f'"{seeds_item.UNREPORTED}"' in text and "COALESCE(" in text


def test_exact_item_preserves_rank_and_deadline_fields(monkeypatch):
    source = rows(**{seeds_item.SEEDS + "ontology/rank": "1.000000001",
                     "http://www.w3.org/2002/12/cal/ical#due": "2026-10-31T00:00:00Z",
                     seeds_item.SCHEMA + "dateCreated": "2026-01-01T00:00:00Z"})
    value, calls = tracker(monkeypatch, source)
    item = seeds_item.read_item(value, "test-one")
    assert (item.rank, item.due_at, item.created_at) == (
        "1.000000001", "2026-10-31T00:00:00Z", "2026-01-01T00:00:00Z")
    assert len(calls) == 1
    assert seeds_item.SEEDS + "ontology/rank" in calls[0]


@pytest.mark.parametrize("rank", ["NaN", "1e2", "1.0000000001"])
def test_exact_item_refuses_malformed_rank(monkeypatch, rank):
    value, _ = tracker(monkeypatch, rows(**{seeds_item.SEEDS + "ontology/rank": rank}))
    with pytest.raises(ValueError):
        seeds_item.read_item(value, "test-one")
