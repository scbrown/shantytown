"""Graph context on dispatch: required, verified, and COUNTED.

`--quipu-node` existed and was optional, which is the same as not existing: the
fleet's own measurement found ~0 graph reads per task while every agent had the
flag available. These tests pin the three decisions that make it a mechanism
instead of an affordance.

1. **A missing context is RECORDED, not silently allowed.** In the default
   advise mode the dispatch proceeds and the ledger says `missing`. That is the
   whole point: an unmeasured habit cannot be argued about, and the argument was
   being had from impressions.
2. **A node the graph does not hold REFUSES, in both modes** — a wrong claim,
   not an absent one — **while an unreachable graph does NOT.** Collapsing those
   two is how a knowledge-graph outage would come to block a coordinator from
   handing out work.
3. **The exemption is free text and is never judged.** What gets measured is the
   SHAPE of the reasons; a dominant one is a finding about the graph.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

import shantytown.cli as cli
from shantytown import graph_adoption as ga
from shantytown.cli import main, OK, REFUSED
from shantytown.tmux import NullPanes


def _root(tmp_path: Path) -> Path:
    root = tmp_path / ".shanty"
    (root / "crew").mkdir(parents=True)
    (root / "crew" / "ellie.json").write_text(json.dumps({"role": "worker", "pane": "%5"}))
    (root / "items").mkdir()
    (root / "items" / "item-1.json").write_text(
        json.dumps({"title": "Restore the den", "status": "open"}))
    return root


def _ledger(root: Path) -> list[dict]:
    path = root / "logs" / ga.LEDGER
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class _Graph:
    """A quipu stand-in. `holds` is what exists; None means unreachable."""

    onto = "http://example.test/ontology"

    def __init__(self, holds=(), unreachable=False):
        self.holds = set(holds)
        self.unreachable = unreachable
        self.queries = []

    def _query(self, sparql):
        if self.unreachable:
            raise OSError("quipu unreachable")
        self.queries.append(sparql)
        if "rdfs:label" in sparql or "rdf-schema#label" in sparql:
            return [{"l": n} for n in self.holds if json.dumps(n) in sparql]
        return [{"s": f"{self.onto}/{n}"} for n in self.holds
                if f"<{self.onto}/{n}>" in sparql]


# --- require: the three shapes -----------------------------------------------

def test_a_named_node_is_context():
    ctx = ga.require(["dolt-server.service"], "")
    assert ctx.nodes == ("dolt-server.service",) and not ctx.exempt


def test_a_stated_reason_is_context_and_is_never_judged():
    ctx = ga.require([], "  nothing modelled for this yet  ")
    assert ctx.exempt and ctx.exemption == "nothing modelled for this yet"
    assert ctx.verification == ga.EXEMPT


def test_neither_raises_and_the_message_names_both_routes():
    with pytest.raises(ga.GraphContextMissing) as e:
        ga.require([], "")
    assert "--quipu-node" in str(e.value) and "--no-graph-context" in str(e.value)


def test_blank_nodes_are_not_context():
    """`--quipu-node ''` must not satisfy a requirement about naming something."""
    with pytest.raises(ga.GraphContextMissing):
        ga.require(["", "   "], "")


# --- verify: absence and silence are different answers -----------------------

def test_a_node_the_graph_holds_verifies():
    ctx = ga.verify(ga.require(["known-node"], ""), _Graph(["known-node"]))
    assert ctx.verification == ga.VERIFIED


def test_a_name_with_a_space_goes_to_the_label_probe_not_into_an_IRI():
    """`<onto/dolt server>` is a malformed IRI. Pasting one in rejects the whole
    batch, so every node in it — including the well-formed ones — would come
    back unverifiable, and entities here are routinely labelled with spaces."""
    graph = _Graph(["dolt server"])
    ctx = ga.verify(ga.require(["dolt server"], ""), graph)
    assert ctx.verification == ga.VERIFIED
    assert len(graph.queries) == 1
    assert "<" not in graph.queries[0].split("VALUES")[1].split("}")[0]


def test_a_spaced_name_does_not_poison_a_batch_with_a_good_one():
    graph = _Graph(["dolt server", "known-node"])
    ctx = ga.verify(ga.require(["dolt server", "known-node"], ""), graph)
    assert ctx.verification == ga.VERIFIED


def test_a_node_the_graph_does_not_hold_refuses():
    with pytest.raises(ga.GraphNodeUnknown) as e:
        ga.verify(ga.require(["typo-node"], ""), _Graph(["known-node"]))
    assert "typo-node" in str(e.value)


def test_an_unreachable_graph_does_not_refuse():
    """The failure this split exists to prevent: a graph outage stopping work."""
    ctx = ga.verify(ga.require(["known-node"], ""), _Graph(unreachable=True))
    assert ctx.verification == ga.UNVERIFIABLE and ctx.nodes == ("known-node",)


def test_no_registry_means_unchecked_not_verified():
    ctx = ga.verify(ga.require(["known-node"], ""), None)
    assert ctx.verification == ga.UNCHECKED, "never claim a check that did not run"


# --- mode --------------------------------------------------------------------

def test_the_default_is_advise(tmp_path, monkeypatch):
    monkeypatch.delenv(ga.MODE_ENV, raising=False)
    assert ga.mode(tmp_path) == ga.ADVISE


def test_the_env_flips_it(monkeypatch):
    monkeypatch.setenv(ga.MODE_ENV, "require")
    assert ga.mode(None) == ga.REQUIRE
    monkeypatch.setenv(ga.MODE_ENV, "nonsense")
    assert ga.mode(None) == ga.ADVISE, "an unreadable value must not enforce"


# --- the CLI ------------------------------------------------------------------

def test_advise_dispatches_and_records_the_gap(tmp_path, monkeypatch, capsys):
    panes = NullPanes(screen="")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: panes)
    monkeypatch.delenv(ga.MODE_ENV, raising=False)
    root = _root(tmp_path)
    assert main(["--root", str(root), "go", "item-1", "ellie"]) == OK
    assert panes.sent, "advise mode must not block the dispatch"
    assert "no graph context" in capsys.readouterr().err
    rows = _ledger(root)
    assert len(rows) == 1
    assert rows[0]["nodes"] == [] and rows[0]["exemption"] == ""
    assert rows[0]["verification"] == ga.MISSING, (
        "a blank exemption would read like a considered one; silence must be "
        "recorded as silence")


def test_require_refuses_and_writes_nothing(tmp_path, monkeypatch, capsys):
    panes = NullPanes(screen="")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: panes)
    monkeypatch.setenv(ga.MODE_ENV, "require")
    root = _root(tmp_path)
    assert main(["--root", str(root), "go", "item-1", "ellie"]) == REFUSED
    assert panes.sent == [], "a refusal must not have typed into anyone's pane"
    assert json.loads((root / "items" / "item-1.json").read_text())["status"] == "open"
    assert _ledger(root) == [], "a dispatch that did not happen is not a data point"
    assert "--quipu-node" in capsys.readouterr().err


def test_the_node_rides_the_payload_and_the_ledger(tmp_path, monkeypatch):
    panes = NullPanes(screen="")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: panes)
    monkeypatch.setattr(cli, "_verification_registry", lambda a: None)
    root = _root(tmp_path)
    assert main(["--root", str(root), "go", "item-1", "ellie",
                 "--quipu-node", "dolt-server.service"]) == OK
    assert "dolt-server.service" in panes.sent[0][1]
    rows = _ledger(root)
    assert rows[0]["nodes"] == ["dolt-server.service"]
    assert rows[0]["event"] == "go" and rows[0]["agent"] == "ellie"
    assert rows[0]["session"] == "%5", "the pane is the nearest thing to a session id"


def test_an_unknown_node_refuses_even_in_advise_mode(tmp_path, monkeypatch, capsys):
    """Advise is about the ABSENCE of context, never about a false claim."""
    panes = NullPanes(screen="")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: panes)
    monkeypatch.setattr(cli, "_verification_registry", lambda a: _Graph(["real-node"]))
    monkeypatch.delenv(ga.MODE_ENV, raising=False)
    root = _root(tmp_path)
    assert main(["--root", str(root), "go", "item-1", "ellie",
                 "--quipu-node", "hallucinated-node"]) == REFUSED
    assert panes.sent == []
    assert "hallucinated-node" in capsys.readouterr().err


def test_a_dry_run_is_recorded_as_a_preview_and_excluded_by_default(
        tmp_path, monkeypatch):
    panes = NullPanes(screen="")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: panes)
    monkeypatch.setattr(cli, "_verification_registry", lambda a: None)
    root = _root(tmp_path)
    assert main(["--root", str(root), "go", "item-1", "ellie", "-n",
                 "--no-graph-context", "just previewing"]) == OK
    rows = _ledger(root)
    assert rows[0]["dry_run"] is True
    assert ga.summarize(rows).eligible == 0, (
        "a preview handed work to nobody; counting it inflates the denominator")
    assert ga.summarize(rows, include_dry_run=True).eligible == 1


# --- the report ---------------------------------------------------------------

def _rows(*specs):
    out = []
    for agent, nodes, exemption in specs:
        out.append({"ts": "now", "epoch": 0, "event": "go", "agent": agent,
                    "item": "i", "session": "", "nodes": list(nodes),
                    "exemption": exemption, "dry_run": False,
                    "verification": ga.VERIFIED if nodes else ga.EXEMPT})
    return out


def test_coverage_counts_a_stated_reason_but_reports_it_separately():
    s = ga.summarize(_rows(("a", ["n1"], ""), ("b", [], "nothing yet"),
                           ("c", [], "")))
    assert (s.eligible, s.with_nodes, s.exempt, s.missing) == (3, 1, 1, 1)
    assert s.coverage == pytest.approx(2 / 3)
    assert s.node_share == pytest.approx(1 / 3), (
        "the exemption is honest, not a pass — the node share is the real number")


def test_an_empty_ledger_is_not_full_coverage():
    s = ga.summarize([])
    assert s.coverage is None and s.node_share is None, (
        "100% over zero events is the flattering answer, and it is not an answer")


def test_zero_node_agents_are_NAMED_not_counted():
    s = ga.summarize(_rows(("a", ["n1"], ""), ("b", [], "reason"), ("c", [], "")))
    assert s.zero_node_agents == ["b", "c"]


def test_the_report_runs_over_a_real_ledger(tmp_path, monkeypatch, capsys):
    panes = NullPanes(screen="")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: panes)
    monkeypatch.setattr(cli, "_verification_registry", lambda a: None)
    root = _root(tmp_path)
    main(["--root", str(root), "go", "item-1", "ellie",
          "--quipu-node", "dolt-server.service"])
    assert main(["--root", str(root), "stats", "--graph"]) == OK
    out = capsys.readouterr().out
    assert "1 eligible dispatches" in out and "dolt-server.service" in out
    assert main(["--root", str(root), "stats", "--graph", "--json"]) == OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["with_nodes"] == 1 and payload["coverage"] == 1.0


def test_the_report_says_so_when_the_ledger_is_empty(tmp_path, capsys):
    assert main(["--root", str(_root(tmp_path)), "stats", "--graph"]) == OK
    out = capsys.readouterr().out
    assert "no eligible dispatches" in out and ga.LEDGER in out


def test_a_malformed_ledger_line_is_skipped_not_fatal(tmp_path):
    root = _root(tmp_path)
    (root / "logs").mkdir()
    (root / "logs" / ga.LEDGER).write_text(
        '{"agent": "a", "nodes": ["n"], "epoch": 0}\nnot json at all\n')
    assert len(ga.read_rows(root)) == 1


# ── MECHANICAL DISPATCH PATHS MUST NOT REFUSE UNDER `require` (aegis-5pchx) ──
# tend serves a pending cycle by building a cycle namespace out of TEND's, and
# tend's parser never declared the graph-context flags. Before the fix that made
# `_graph_context` see nothing and refuse under require — and because tend
# deliberately leaves a refused request pending, it would have refused on every
# pass, forever, for every agent. That is Rule Zero self-feeding breaking, which
# is worse than the coverage gap the require mode exists to close.

def _mechanical_cycle_namespace(req_nodes):
    """The namespace shape cli.py builds when tend serves a cycle request."""
    import argparse
    tend = argparse.Namespace(cmd="tend", root=".", agent=None)
    n = list(req_nodes or [])
    return argparse.Namespace(**{**vars(tend), "cmd": "cycle", "agent": "someone",
        "reason": "", "self_": False, "checkpoint_bead": "", "quipu_node": n,
        "no_graph_context": "" if n else
            "mechanical: tend serving a cycle the agent already requested and gated",
        "allow_loss": False, "dry_run": False})


def test_mechanically_served_cycle_carries_the_requesters_nodes():
    a = _mechanical_cycle_namespace(["dolt-server.service"])
    ctx = ga.require(a.quipu_node, a.no_graph_context)
    assert list(ctx.nodes) == ["dolt-server.service"]
    assert not ctx.exemption


def test_mechanically_served_cycle_without_stored_nodes_states_a_machine_reason():
    a = _mechanical_cycle_namespace([])
    ctx = ga.require(a.quipu_node, a.no_graph_context)
    assert not ctx.nodes
    # Silence is not an exemption anywhere in this system; the machine says why.
    assert "mechanical" in ctx.exemption


def test_control_the_unfixed_shape_would_still_refuse():
    """Without this the two tests above pass even if the gate stopped working."""
    import argparse
    import pytest
    tend = argparse.Namespace(cmd="tend", root=".", agent=None)
    unfixed = argparse.Namespace(**{**vars(tend), "cmd": "cycle", "agent": "x",
                                    "reason": "", "self_": False,
                                    "checkpoint_bead": "", "allow_loss": False,
                                    "dry_run": False})
    with pytest.raises(ga.GraphContextMissing):
        ga.require(getattr(unfixed, "quipu_node", []),
                   getattr(unfixed, "no_graph_context", ""))


# ── THE SCOPE TRAVELS WITH THE NUMBER (aegis-5pchx) ──────────────────────────
# A haul self-advance never reaches the gate, so a green coverage figure means
# "coordinator dispatch and explicit cycles complied", not "the fleet cites
# graph context". A note that lives only on a bead is one the number outruns,
# so it is asserted at every rendering — including --json, where over-reading
# is likeliest because a machine consumer never sees the prose.

def test_the_scope_note_is_printed_when_the_ledger_is_empty(tmp_path, capsys):
    assert main(["--root", str(_root(tmp_path)), "stats", "--graph"]) == OK
    out = capsys.readouterr().out
    assert "self-advances" in out and "not counted" in out


def test_the_scope_note_is_printed_beside_a_real_number(tmp_path, capsys):
    root = _root(tmp_path)
    (root / "logs").mkdir(exist_ok=True)
    (root / "logs" / ga.LEDGER).write_text(
        '{"agent": "a", "nodes": ["n"], "epoch": 99999999999, "event": "go",'
        ' "exemption": "", "verification": "verified", "dry_run": false}\n')
    assert main(["--root", str(root), "stats", "--graph"]) == OK
    out = capsys.readouterr().out
    assert "eligible dispatches" in out          # control: the number rendered
    assert ga.SCOPE_NOTE in out                  # and the scope came with it


def test_the_scope_note_is_in_the_json(tmp_path, capsys):
    root = _root(tmp_path)
    (root / "logs").mkdir(exist_ok=True)
    (root / "logs" / ga.LEDGER).write_text(
        '{"agent": "a", "nodes": ["n"], "epoch": 99999999999, "event": "go",'
        ' "exemption": "", "verification": "verified", "dry_run": false}\n')
    assert main(["--root", str(root), "stats", "--graph", "--json"]) == OK
    payload = json.loads(capsys.readouterr().out)
    assert "self-advances" in payload["scope"]
