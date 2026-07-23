"""st project — the guard that stops a dirty graph from restructuring a live crew.

internal-ref. `project` used to be a bare `for ag in agents: files.set(ag)`: no
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

    rc = main(["--root", str(root), "project"])

    assert rc == REFUSED
    assert role_of(root, "sattler") == "administrator", "refusal must write NOTHING"
    err = capsys.readouterr().err
    assert "REFUSED" in err and "sattler" in err


def test_same_change_is_allowed_when_the_agent_is_not_live(tmp_path, monkeypatch):
    """The guard keys on LIVENESS, not on the size of the change. An identical
    demotion of a stopped agent is ordinary projection and must not refuse."""
    root = crew(tmp_path, sattler={"role": "administrator", "pane": "shanty-sattler"})
    graph(monkeypatch, Agent(name="sattler", role="worker"))
    panes(monkeypatch)  # nothing live

    rc = main(["--root", str(root), "project"])

    assert rc == OK
    assert role_of(root, "sattler") == "worker"


def test_force_overrides_the_refusal(tmp_path, monkeypatch):
    root = crew(tmp_path, sattler={"role": "administrator", "pane": "shanty-sattler"})
    graph(monkeypatch, Agent(name="sattler", role="worker"))
    panes(monkeypatch, "shanty-sattler")

    rc = main(["--root", str(root), "project", "--force"])

    assert rc == OK
    assert role_of(root, "sattler") == "worker"


def test_dry_run_writes_nothing_and_creates_no_ghost_cards(tmp_path, monkeypatch):
    root = crew(tmp_path, sattler={"role": "administrator", "pane": "shanty-sattler"})
    graph(monkeypatch,
          Agent(name="sattler", role="worker"),
          Agent(name="a-backup-host", role="worker")) # a HOST, per the live graph
    panes(monkeypatch, "shanty-sattler")

    rc = main(["--root", str(root), "project", "-n"])

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

    main(["--root", str(root), "project", "-n"])

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

    main(["--root", str(root), "project", "-n"])

    assert "demoted supervisor" not in capsys.readouterr().out


def test_clean_projection_is_idempotent_and_quiet(tmp_path, monkeypatch, capsys):
    root = crew(tmp_path, sattler={"role": "worker", "pane": "shanty-sattler"})
    graph(monkeypatch, Agent(name="sattler", role="worker"))
    panes(monkeypatch, "shanty-sattler")

    rc = main(["--root", str(root), "project"])

    assert rc == OK
    assert "Nothing to do" in capsys.readouterr().out
