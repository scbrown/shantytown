"""st project — the guard that stops a dirty graph from restructuring a live crew.

aegis-0v97. `project` used to be a bare `for ag in agents: files.set(ag)`: no
preview, no confirmation, no notion that any of those agents might be RUNNING.
That is safe only while the graph is clean, and ours was not — measured on the
live store, the graph declared a HOST (a-backup-host) and a process this fleet has stated
does not exist (mayor) as crew workers, and projecting it would have demoted the
live administrator and cut ten running agents loose.

The contract these pin:
  · a diff is ALWAYS printed, so the blast radius is visible before it lands
  · --dry-run writes nothing
  · a projection that restructures a LIVE agent REFUSES (1) and writes nothing
  · --force still works — this is a speed bump, not a wall
  · the dangling case is surfaced: an agent absent from the graph is left
    untouched and so keeps pointing at a supervisor the projection demoted.
    No individual row shows that, which is exactly why it needs its own check.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown import cli
from shantytown.cli import main, OK, REFUSED
from shantytown.protocols import Agent


def crew(tmp_path: Path, **agents) -> Path:
    d = tmp_path / "crew"; d.mkdir()
    for n, spec in agents.items():
        (d / f"{n}.json").write_text(json.dumps(spec))
    return tmp_path


def graph(monkeypatch, *agents):
    """Point `project` at a fake graph. Read-only, so a stub registry is enough."""
    class FakeQuipu:
        def all(self):
            return list(agents)
    monkeypatch.setattr(cli, "QuipuRegistry", FakeQuipu)


def panes(monkeypatch, *live):
    """Declare which panes are live. Liveness is read from the CARD's pane, since
    the graph has no idea what is running — the whole reason it must not
    restructure the crew unsupervised."""
    class FakeTmux:
        def __init__(self, socket=None):     # the CLI now names the fleet's socket
            self.socket = socket

        def exists(self, pane):
            return pane in live
    monkeypatch.setattr(cli, "Tmux", FakeTmux)


def role_of(root, name):
    return json.loads((root / "crew" / f"{name}.json").read_text())["role"]


def test_refuses_when_a_live_agent_would_be_restructured(tmp_path, monkeypatch, capsys):
    root = crew(tmp_path, sattler={"role": "administrator", "pane": "shanty-sattler"})
    graph(monkeypatch, Agent(name="sattler", role="worker"))
    panes(monkeypatch, "shanty-sattler")

    rc = main(["--root", str(root), "roles", "sync"])

    assert rc == REFUSED
    assert role_of(root, "sattler") == "administrator", "refusal must write NOTHING"
    err = capsys.readouterr().err
    assert "REFUSED" in err and "sattler" in err


# WHY THESE TWO DEMOTE `dearing` AND NOT `sattler` (aegis-ftmfn). They used to
# demote the fleet's only administrator to a worker with no lead — which is an
# ORPHAN, and the orphan guard now refuses it no matter what --force says. The
# fixture was never the point of either test: both exist to pin the LIVENESS
# guard, and a demotion that also cuts an agent loose cannot isolate it. So the
# demotion is now of a lead who keeps its supervisor — a real restructure, no
# orphan — and each test measures the one rule it names.
#
# The old fixture is not lost: it is the aegis-0v97 scenario verbatim ("would
# have demoted the live administrator to an orphan worker"), and it now lives in
# test_project_orphan_guard.py as the thing that MUST refuse.

def test_same_change_is_allowed_when_the_agent_is_not_live(tmp_path, monkeypatch):
    """The guard keys on LIVENESS, not on the size of the change. An identical
    demotion of a stopped agent is ordinary projection and must not refuse."""
    root = crew(tmp_path,
                sattler={"role": "administrator", "pane": "shanty-sattler"},
                dearing={"role": "lead", "reports_to": "sattler", "pane": "shanty-dearing"})
    graph(monkeypatch,
          Agent(name="sattler", role="administrator"),
          Agent(name="dearing", role="worker", reports_to="sattler"))
    panes(monkeypatch)  # nothing live

    rc = main(["--root", str(root), "roles", "sync"])

    assert rc == OK
    assert role_of(root, "dearing") == "worker"


def test_force_overrides_the_refusal(tmp_path, monkeypatch):
    root = crew(tmp_path,
                sattler={"role": "administrator", "pane": "shanty-sattler"},
                dearing={"role": "lead", "reports_to": "sattler", "pane": "shanty-dearing"})
    graph(monkeypatch,
          Agent(name="sattler", role="administrator"),
          Agent(name="dearing", role="worker", reports_to="sattler"))
    panes(monkeypatch, "shanty-dearing")

    rc = main(["--root", str(root), "roles", "sync", "--force"])

    assert rc == OK
    assert role_of(root, "dearing") == "worker"


def test_dry_run_writes_nothing_and_creates_no_ghost_cards(tmp_path, monkeypatch):
    root = crew(tmp_path, sattler={"role": "administrator", "pane": "shanty-sattler"})
    graph(monkeypatch,
          Agent(name="sattler", role="worker"),
          Agent(name="a-backup-host", role="worker")) # a HOST, per the live graph
    panes(monkeypatch, "shanty-sattler")

    rc = main(["--root", str(root), "roles", "sync", "-n"])

    assert rc == OK
    assert role_of(root, "sattler") == "administrator"
    assert not (root / "crew" / "a-backup-host.json").exists(), "dry-run must mint no cards"


def test_dangling_supervisor_is_surfaced(tmp_path, monkeypatch, capsys):
    """tim is NOT in the graph, so projection leaves his card alone — and he keeps
    reporting to a sattler who just became a worker. Nobody's own diff row shows
    this; without the explicit check it lands silently."""
    root = crew(tmp_path,
                sattler={"role": "administrator", "pane": "shanty-sattler"},
                tim={"role": "worker", "reports_to": "sattler", "pane": "shanty-tim"})
    graph(monkeypatch, Agent(name="sattler", role="worker"))
    panes(monkeypatch, "shanty-sattler", "shanty-tim")

    main(["--root", str(root), "roles", "sync", "-n"])

    out = capsys.readouterr().out
    assert "demoted supervisor" in out
    assert "tim" in out and "still reports_to sattler" in out


def test_no_dangling_report_when_supervisor_keeps_rank(tmp_path, monkeypatch, capsys):
    """Positive control for the dangling check: same shape, but sattler stays an
    administrator, so there is nothing to warn about. A check that always fires
    is not a check."""
    root = crew(tmp_path,
                sattler={"role": "administrator", "pane": "shanty-sattler"},
                tim={"role": "worker", "reports_to": "sattler", "pane": "shanty-tim"})
    graph(monkeypatch, Agent(name="sattler", role="administrator", reports_to="x"))
    panes(monkeypatch, "shanty-sattler", "shanty-tim")

    main(["--root", str(root), "roles", "sync", "-n"])

    assert "demoted supervisor" not in capsys.readouterr().out


def test_clean_projection_is_idempotent_and_quiet(tmp_path, monkeypatch, capsys):
    root = crew(tmp_path, sattler={"role": "worker", "pane": "shanty-sattler"})
    graph(monkeypatch, Agent(name="sattler", role="worker"))
    panes(monkeypatch, "shanty-sattler")

    rc = main(["--root", str(root), "roles", "sync"])

    assert rc == OK
    assert "Nothing to do" in capsys.readouterr().out


def test_zero_agents_from_a_reachable_graph_is_could_not_tell(tmp_path, monkeypatch, capsys):
    """aegis-wxrm closing an ellie-documented false pass: an empty-but-reachable
    graph (wrong SHANTY_ONTO_NS answers 'nobody exists' with a straight face)
    used to print 'already projected: 0 cards match the graph. Nothing to do.'
    and exit 0. Zero crew from a reachable graph is could-not-tell, loudly."""
    from types import SimpleNamespace
    root = crew(tmp_path)          # no cards, and —
    graph(monkeypatch)             # — a reachable graph with ZERO agents
    panes(monkeypatch)
    a = SimpleNamespace(root=root, dry_run=True, force=False)
    rc = cli._cmd_project(a)
    err = capsys.readouterr().err
    assert rc == cli.CANNOT_TELL
    assert "ZERO CrewMembers" in err and "namespace" in err


# ── consistency is not correctness (aegis-uymsl) ─────────────────────────────

def test_a_clean_quipu_match_says_it_is_CONSISTENCY_not_correctness(
        tmp_path, monkeypatch, capsys):
    """"20 cards match the graph. Nothing to do." read as validation, and it is a
    ROUND TRIP: `roles sync` is what projects the graph's crew facts FROM the
    cards, so a clean match proves the sync worked and nothing else.

    Measured on the live fleet: this printed 20/20 clean and a direct SPARQL count
    agreed at 20, while the operator's actual roster decision existed in no
    machine-readable form at all. Two instruments agreeing is not two instruments
    being right — and the sibling defect above (0 cards from a wrong namespace
    reported as success) is the same shape one step earlier."""
    root = crew(tmp_path, sattler={"role": "worker", "pane": "shanty-sattler"})
    graph(monkeypatch, Agent(name="sattler", role="worker"))
    panes(monkeypatch, "shanty-sattler")

    rc = main(["--root", str(root), "roles", "sync"])

    cap = capsys.readouterr()
    assert rc == OK
    assert "Nothing to do" in cap.out, "the existing outcome line must survive"
    assert "CONSISTENCY, not correctness" in cap.err
    # It must name the REASON and the REMEDY, not merely hedge. A warning that
    # says "this might not be right" teaches nothing and gets tuned out.
    assert "projected FROM these cards" in cap.err
    assert "--from file:" in cap.err


def test_a_clean_FILE_match_does_NOT_warn(tmp_path, monkeypatch, capsys):
    """THE DISCRIMINATING CONTROL, and without it the test above proves nothing.

    A file IS an independent referent — someone wrote the roster down, so matching
    against it is a real check and warning about it would be false. If this test
    ever fails, the warning has become unconditional noise, which is how a true
    caveat trains people to ignore it."""
    from shantytown.hierarchy import FileHierarchy

    h = tmp_path / "hierarchy.json"
    h.write_text(json.dumps({"sattler": None, "ian": "sattler"}))
    # Build the cards FROM the file's own derivation, so the match is clean by
    # construction whatever roles derive_agents assigns — the test is about the
    # warning, not about role inference.
    d = tmp_path / "crew"; d.mkdir()
    for ag in FileHierarchy(h).all():
        (d / f"{ag.name}.json").write_text(json.dumps(
            {"role": ag.role, "reports_to": ag.reports_to,
             "pane": f"shanty-{ag.name}"}))
    panes(monkeypatch)

    rc = main(["--root", str(tmp_path), "roles", "sync", "--from", f"file:{h}"])

    cap = capsys.readouterr()
    assert rc == OK
    assert "Nothing to do" in cap.out
    assert "CONSISTENCY, not correctness" not in cap.err, (
        "a file source is a real referent — warning here would be false, and a "
        "warning that fires on the good case is one nobody reads on the bad one")
