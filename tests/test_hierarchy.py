"""WHERE the tier comes from (aegis-t4eve).

The load-bearing property here is not "it can read a file" — it is that the
source is never silently substituted, and that whichever source answered is
reported. So the refusals are shown refusing for the RIGHT reason (an explicit
`--from quipu` that goes down must RAISE, not quietly fall back to a stale
file), and the fallback is shown carrying the reason it happened.

The second property: a file and a graph describing the same hierarchy must
produce IDENTICAL agents, because both go through `derive_agents`. A test that
only checked the file path would not catch the two sources drifting apart.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shantytown.answer import Answer
from shantytown.hierarchy import (
    FileHierarchy, HierarchyUnavailable, SourceInfo, default_file,
    load_file_rows, parse_spec, resolve, _rows_from_mapping, _rows_from_ttl,
)
from shantytown.quipu import derive_agents


# ── parse_spec ───────────────────────────────────────────────────────────────

def test_parse_spec_forms():
    assert parse_spec(None) == (None, None)
    assert parse_spec("") == (None, None)
    assert parse_spec("quipu") == ("quipu", None)
    assert parse_spec("file:/tmp/h.yaml") == ("file", "/tmp/h.yaml")


def test_parse_spec_refuses_a_mistyped_source_instead_of_guessing():
    # Guessing at 'quipuu' is how you project from the wrong source.
    with pytest.raises(ValueError, match="unknown --from source"):
        parse_spec("quipuu")
    with pytest.raises(ValueError, match="needs a path"):
        parse_spec("file:")


# ── file shapes ──────────────────────────────────────────────────────────────

def test_mapping_shapes_all_reduce_to_the_same_rows():
    nested = {"dearing": {"reports_to": None}, "ian": {"reports_to": "dearing"}}
    flat = {"dearing": None, "ian": "dearing"}
    listed = [{"name": "dearing"}, {"name": "ian", "reports_to": "dearing"}]
    want = [{"s": "dearing"}, {"s": "ian", "rt": "dearing"}]
    for shape in (nested, flat, listed):
        assert sorted(_rows_from_mapping(shape), key=lambda r: r["s"]) == want


def test_a_hand_written_role_is_IGNORED_so_a_file_cannot_outrank_derivation():
    # role is derived from the shape of reports_to. Honoring a written role
    # would let a file and the graph disagree about who is a lead.
    rows = _rows_from_mapping({"ian": {"reports_to": "dearing", "role": "administrator"}})
    assert rows == [{"s": "ian", "rt": "dearing"}]
    assert derive_agents(rows + [{"s": "dearing"}])[1].role == "worker"


def test_ttl_extracts_the_crewmember_shape():
    ttl = """@prefix aegis: <http://aegis.gastown.local/ontology/> .
# a comment
aegis:dearing a aegis:CrewMember .
aegis:ian a aegis:CrewMember ;
    aegis:reports_to aegis:dearing .
"""
    rows = sorted(_rows_from_ttl(ttl), key=lambda r: r["s"])
    assert rows == [{"s": "dearing"}, {"s": "ian", "rt": "dearing"}]


def test_ttl_with_no_crewmember_REFUSES_rather_than_returning_an_empty_crew():
    # An empty projection is the exact failure this module exists to prevent.
    with pytest.raises(HierarchyUnavailable, match="no aegis:CrewMember"):
        _rows_from_ttl("@prefix aegis: <http://x/> .\naegis:example-host a aegis:Host .\n")


def test_load_file_rows_json_and_the_two_refusals(tmp_path):
    p = tmp_path / "hierarchy.json"
    p.write_text(json.dumps({"dearing": None, "ian": "dearing"}))
    assert sorted(load_file_rows(p), key=lambda r: r["s"]) == [
        {"s": "dearing"}, {"s": "ian", "rt": "dearing"}]

    with pytest.raises(HierarchyUnavailable, match="no such hierarchy file"):
        load_file_rows(tmp_path / "nope.json")
    bad = tmp_path / "hierarchy.txt"
    bad.write_text("whatever")
    with pytest.raises(HierarchyUnavailable, match="unsupported hierarchy file type"):
        load_file_rows(bad)


# ── the two sources must agree ───────────────────────────────────────────────

def test_file_and_graph_rows_derive_IDENTICAL_agents(tmp_path):
    graph_rows = [{"s": "http://x/dearing"}, {"s": "http://x/ian", "rt": "http://x/dearing"}]
    p = tmp_path / "hierarchy.json"
    p.write_text(json.dumps({"dearing": None, "ian": "dearing"}))
    assert FileHierarchy(p).all().exact() == derive_agents(graph_rows)


# ── resolve: the substitution rules ──────────────────────────────────────────

class _Src:
    def __init__(self, agents): self._a = agents
    def all(self):
        return Answer.complete_read(list(self._a), how="test hierarchy source")


class _Dead:
    def __init__(self, *_a, **_k): pass
    def all(self): raise RuntimeError("quipu is down")


def _agents():
    return derive_agents([{"s": "dearing"}, {"s": "ian", "rt": "dearing"}])


def test_explicit_quipu_that_is_DOWN_refuses_and_does_NOT_fall_back(tmp_path):
    # THE property: you asked for the graph. Getting a stale file without being
    # told is how the file quietly becomes the authority.
    p = tmp_path / "hierarchy.json"
    p.write_text(json.dumps({"dearing": None}))
    with pytest.raises(RuntimeError, match="quipu is down"):
        resolve("quipu", file_default=p, quipu_factory=_Dead)


def test_auto_prefers_the_graph_and_says_so():
    src, info = resolve(None, quipu_factory=lambda: _Src(_agents()))
    assert src.all().exact() == _agents()
    assert info.kind == "quipu" and info.fallback_reason is None
    assert "ontology-first default" in info.render()


def test_auto_falls_back_to_the_file_and_CARRIES_THE_REASON(tmp_path):
    p = tmp_path / "hierarchy.json"
    p.write_text(json.dumps({"dearing": None, "ian": "dearing"}))
    src, info = resolve(None, file_default=p, quipu_factory=_Dead)
    assert src.all().exact() == _agents()
    assert info.kind == "file"
    assert "quipu is down" in info.fallback_reason
    assert "FELL BACK from quipu" in info.render()


def test_auto_with_no_fallback_configured_refuses_and_says_WHICH_is_missing():
    with pytest.raises(HierarchyUnavailable, match="no fallback hierarchy file"):
        resolve(None, file_default=None, quipu_factory=_Dead)


def test_auto_refuses_when_BOTH_sources_are_unreadable(tmp_path):
    with pytest.raises(HierarchyUnavailable, match="AND the fallback"):
        resolve(None, file_default=tmp_path / "missing.json", quipu_factory=_Dead)


def test_explicit_file_is_used_and_marked_explicit(tmp_path):
    p = tmp_path / "hierarchy.json"
    p.write_text(json.dumps({"dearing": None, "ian": "dearing"}))
    src, info = resolve(f"file:{p}", quipu_factory=_Dead)
    assert src.all().exact() == _agents()
    assert info.explicit and "named with --from" in info.render()


# ── default_file discovery ───────────────────────────────────────────────────

def test_default_file_env_wins_then_convention_then_None(tmp_path, monkeypatch):
    monkeypatch.delenv("SHANTY_HIERARCHY_FILE", raising=False)
    assert default_file(tmp_path) is None          # nothing there: None, not a guess
    conv = tmp_path / "hierarchy.json"
    conv.write_text("{}")
    assert default_file(tmp_path) == conv
    monkeypatch.setenv("SHANTY_HIERARCHY_FILE", "/named/by/operator.yaml")
    assert default_file(tmp_path) == Path("/named/by/operator.yaml")
