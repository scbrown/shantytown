"""A suggested graph node for work that names none (aegis-4hhqoe.12).

The linker itself (camayoc `entity_link.py`) is stubbed: these prove what st does
with its answer, namely that a suggestion stays a suggestion, that every failure
is "no suggestion" and never an exception, and that the accept/reject read
counts what agents later cite.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from shantytown import entity_suggest as es
from shantytown import graph_adoption as ga
from shantytown import resume_brief


@pytest.fixture
def camayoc(tmp_path, monkeypatch):
    script = tmp_path / "camayoc" / "scripts" / "entity_link.py"
    script.parent.mkdir(parents=True)
    script.write_text("# stub; never executed, run= is injected\n")
    monkeypatch.setenv(es.SOURCE_ENV, str(tmp_path / "camayoc"))
    monkeypatch.delenv(es.MODE_ENV, raising=False)
    monkeypatch.delenv("QUIPU_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("QUIPU_AUTH_TOKEN_FILE", str(tmp_path / "no-token"))
    return script


def runner(stdout="", returncode=0, stderr="", raises=None, seen=None):
    def run(argv, **kw):
        if seen is not None:
            seen.append(argv)
        if raises:
            raise raises
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
    return run


def answer(**kw):
    return json.dumps({"item": "aegis-x1", "candidates": [], "choice": None,
                       "confidence": None, "error": None, **kw}) + "\n"


def test_a_pick_becomes_a_pasteable_local_name(camayoc, tmp_path):
    s = es.suggest(tmp_path, "aegis-x1",
                   run=runner(answer(choice="aegis:dolt-server", confidence=0.91)))
    assert s.node == "dolt-server" and s.confidence == 0.91
    assert "--quipu-node dolt-server" in s.render()


def test_without_a_token_it_suggests_but_does_not_write(camayoc, tmp_path):
    seen = []
    s = es.suggest(tmp_path, "aegis-x1", run=runner(answer(choice="aegis:a"), seen=seen))
    assert "--write" not in seen[0] and not s.written


def test_with_a_token_the_link_is_written_and_reported(camayoc, tmp_path, monkeypatch):
    monkeypatch.setenv("QUIPU_AUTH_TOKEN", "t")
    seen = []
    s = es.suggest(tmp_path, "aegis-x1",
                   run=runner(answer(choice="aegis:a", write={"conforms": True}), seen=seen))
    assert "--write" in seen[0] and s.written


def test_suggest_mode_never_writes(camayoc, tmp_path, monkeypatch):
    monkeypatch.setenv("QUIPU_AUTH_TOKEN", "t")
    monkeypatch.setenv(es.MODE_ENV, es.SUGGEST)
    seen = []
    es.suggest(tmp_path, "aegis-x1", run=runner(answer(choice="aegis:a"), seen=seen))
    assert "--write" not in seen[0]


def test_off_asks_nothing(camayoc, tmp_path, monkeypatch):
    monkeypatch.setenv(es.MODE_ENV, es.OFF)
    seen = []
    assert es.suggest(tmp_path, "aegis-x1", run=runner(seen=seen)) is None
    assert seen == []


@pytest.mark.parametrize("run, note", [
    (runner(answer()), "none of the retrieved"),
    (runner(answer(error="TimeoutError: t")), "entity_link error"),
    (runner("", returncode=2, stderr="boom"), "exit 2"),
    (runner("not json"), "no result"),
    (runner(raises=subprocess.TimeoutExpired("x", 1)), "timed out"),
    (runner(raises=OSError("no python3")), "did not run"),
])
def test_every_failure_is_no_suggestion_never_an_exception(camayoc, tmp_path, run, note):
    s = es.suggest(tmp_path, "aegis-x1", run=run)
    assert not s.node and note in s.note


def test_a_missing_camayoc_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setenv(es.SOURCE_ENV, str(tmp_path / "absent"))
    monkeypatch.delenv(es.MODE_ENV, raising=False)
    s = es.suggest(tmp_path, "aegis-x1", run=runner(raises=AssertionError("ran")))
    assert "not found" in s.note


def test_the_ledger_keeps_the_suggestion_beside_nodes_not_in_them(tmp_path):
    sug = es.Suggestion(node="dolt-server", confidence=0.9, written=True)
    ga.record(tmp_path, "go", "a1", "aegis-x1", ga.unstated(), suggestion=sug)
    row = ga.read_rows(tmp_path)[0]
    assert row["nodes"] == [] and row["suggested"] == "dolt-server"
    assert row["suggested_written"] is True


def test_a_suggest_only_row_is_not_a_dispatch(tmp_path):
    sug = es.Suggestion(node="n")
    ga.record(tmp_path, ga.SUGGEST, "a1", "aegis-x1", ga.unstated(), suggestion=sug)
    ga.record(tmp_path, "go", "a1", "aegis-x2", ga.unstated())
    assert ga.summarize(ga.read_rows(tmp_path)).eligible == 1


def _row(epoch, item, event="cycle", nodes=(), exemption="", suggested=None):
    r = {"epoch": epoch, "item": item, "event": event, "nodes": list(nodes),
         "exemption": exemption}
    if suggested:
        r["suggested"] = suggested
    return r


def test_outcomes_read_the_next_stated_context_for_the_same_item():
    rows = [
        _row(1, "a", event=ga.SUGGEST, suggested="n1"), _row(2, "a", nodes=["n1"]),
        _row(3, "b", event=ga.SUGGEST, suggested="n2"), _row(4, "b", nodes=["other"]),
        _row(5, "c", event=ga.SUGGEST, suggested="n3"), _row(6, "c", exemption="none fits"),
        _row(7, "d", event=ga.SUGGEST, suggested="n4"),
        _row(8, "e", event=ga.SUGGEST, suggested="n5"), _row(9, "x", nodes=["n5"]),
    ]
    o = ga.suggestion_outcomes(rows)
    assert (o.suggested, o.accepted, o.rejected, o.pending) == (5, 1, 2, 2)
    assert o.precision == pytest.approx(1 / 3)


def test_one_decision_is_never_counted_against_two_suggestions():
    rows = [_row(1, "a", event=ga.SUGGEST, suggested="n1"),
            _row(2, "a", event=ga.SUGGEST, suggested="n1"),
            _row(3, "a", nodes=["n1"])]
    o = ga.suggestion_outcomes(rows)
    assert (o.suggested, o.accepted) == (1, 1)


def test_no_decisions_is_no_precision():
    assert ga.suggestion_outcomes([_row(1, "a", event=ga.SUGGEST, suggested="n")]).precision is None


def test_the_brief_labels_a_suggestion_as_one():
    text = resume_brief.compose("a1", item="aegis-x1", suggested="dolt-server")
    assert "suggested graph context: dolt-server" in text and "not confirmed" in text


def test_asserted_nodes_win_over_a_suggestion_in_the_brief():
    text = resume_brief.compose("a1", item="aegis-x1", quipu_nodes=["real"],
                                suggested="dolt-server")
    assert "dolt-server" not in text and "graph context: real" in text


def _brief_args(tmp_path, nodes=()):
    return SimpleNamespace(root=tmp_path, quipu_node=list(nodes), checkpoint_bead="")


def test_the_resume_brief_suggests_for_the_plate_and_logs_it(tmp_path, monkeypatch):
    from shantytown import cli
    monkeypatch.setattr(cli, "_tracker", lambda a: None)
    monkeypatch.setattr(cli, "_tracker_plate", lambda trk, who: SimpleNamespace(id="aegis-x1"))
    monkeypatch.setattr(cli.entity_suggest, "suggest",
                        lambda root, item: es.Suggestion(node="dolt-server", confidence=0.8))
    path = cli._write_resume_brief(_brief_args(tmp_path), SimpleNamespace(workspace=""),
                                   "ellie", "was mid-way")
    assert "suggested graph context: dolt-server" in open(path).read()
    row = ga.read_rows(tmp_path)[0]
    assert row["event"] == ga.SUGGEST and row["item"] == "aegis-x1"
    assert row["suggested"] == "dolt-server" and row["nodes"] == []


def test_a_brief_with_asserted_nodes_asks_for_no_suggestion(tmp_path, monkeypatch):
    from shantytown import cli
    monkeypatch.setattr(cli, "_tracker", lambda a: None)
    monkeypatch.setattr(cli, "_tracker_plate", lambda trk, who: SimpleNamespace(id="aegis-x1"))
    monkeypatch.setattr(cli.entity_suggest, "suggest",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked")))
    cli._write_resume_brief(_brief_args(tmp_path, ["real"]), SimpleNamespace(workspace=""),
                            "ellie", "x")
    assert ga.read_rows(tmp_path) == []


def test_a_crashing_suggestion_never_costs_the_brief(tmp_path, monkeypatch):
    from shantytown import cli
    monkeypatch.setattr(cli, "_tracker", lambda a: None)
    monkeypatch.setattr(cli, "_tracker_plate", lambda trk, who: SimpleNamespace(id="aegis-x1"))
    monkeypatch.setattr(cli.entity_suggest, "suggest",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    path = cli._write_resume_brief(_brief_args(tmp_path), SimpleNamespace(workspace=""),
                                   "ellie", "x")
    assert path and "suggested graph context" not in open(path).read()
