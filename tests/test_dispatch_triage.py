"""st go now consults triage before it sends — shantytown #1.

Before this, `st go` went straight to send-keys, so dispatching to an agent that
was mid-response interrupted its work. These tests prove the gate: an in-flight
or wedged pane is REFUSED, and crucially the tracker is NOT written — a refusal
that still marked the item in_progress would be a half-dispatch, the exact thing
plan() already refuses to do on precondition failures.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown.dispatch import Dispatcher, TriageRefused
from shantytown.files import FilesRegistry, FilesTracker
from shantytown.tmux import NullPanes


class _CountingTracker(FilesTracker):
    def __init__(self, root):
        super().__init__(root)
        self.updates = 0

    def update(self, item_id, **fields):
        self.updates += 1
        return super().update(item_id, **fields)


@pytest.fixture
def world(tmp_path: Path):
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "ellie.json").write_text(json.dumps({"role": "worker", "pane": "%5"}))
    trk = _CountingTracker(tmp_path / "items")
    trk.update("item-1", title="Restore the den", status="open")
    trk.updates = 0
    return crew, trk


def test_healthy_pane_dispatches(world):
    """Empty/quiet pane triages NUDGE — the happy path still works."""
    crew, trk = world
    panes = NullPanes(screen="")            # nothing on screen = healthy
    d = Dispatcher(FilesRegistry(crew), trk, panes)
    d.go("item-1", "ellie")
    assert trk.get("item-1").status == "in_progress"
    assert len(panes.sent) == 1


def test_in_flight_pane_is_refused_AND_not_written(world):
    """THE #1 FIX. A pane showing in-flight work must not be interrupted, and the
    item must not be marked in_progress for a send that never happened."""
    crew, trk = world
    panes = NullPanes(screen="thinking… (esc to interrupt)")
    d = Dispatcher(FilesRegistry(crew), trk, panes)

    with pytest.raises(TriageRefused) as ei:
        d.go("item-1", "ellie")

    assert ei.value.decision.action.value == "refuse"
    assert trk.updates == 0, "refused a send but still wrote — half-dispatch"
    assert panes.sent == [], "refused but sent anyway — interrupted the agent"


def test_busy_marker_above_tail_refuses_without_side_effects(world):
    crew, trk = world
    screen = "\n".join(["esc to interrupt"] + ["tool continuation"] * 6
                       + ["› ", "footer1", "footer2", "footer3"])
    panes = NullPanes(screen=screen)
    d = Dispatcher(FilesRegistry(crew), trk, panes)
    with pytest.raises(TriageRefused) as ei:
        d.go("item-1", "ellie")
    assert ei.value.decision.action.value == "refuse"
    assert ei.value.decision.inputs["marker"] == "esc to interrupt"
    assert trk.updates == 0
    assert trk.get("item-1").status == "open"
    assert panes.sent == []


def test_wedged_pane_is_refused_not_dispatched(world):
    crew, trk = world
    panes = NullPanes(screen="[Process completed]")
    d = Dispatcher(FilesRegistry(crew), trk, panes)
    with pytest.raises(TriageRefused) as ei:
        d.go("item-1", "ellie")
    assert ei.value.decision.action.value == "restart"
    assert trk.updates == 0
    assert panes.sent == []


def test_dry_run_triages_without_touching_anything(world):
    """--dry-run shows the judgement and writes nothing, on a busy pane too."""
    crew, trk = world
    panes = NullPanes(screen="Running… (esc to interrupt)")
    d = Dispatcher(FilesRegistry(crew), trk, panes)
    decision = d.triage("item-1", "ellie")           # read-only
    assert decision.action.value == "refuse"
    assert trk.updates == 0
    assert panes.sent == []


@pytest.mark.parametrize("reassign,screen,governor_refusal,expected,marker", [
    (True, "", "", 0, "assignee=ellie"),
    (False, "", "", 1, "--reassign"),
    (True, "thinking… (esc to interrupt)", "", 0, "REFUSE"),
    (True, "", "fixture governor refusal", 1, "fixture governor refusal"),
])
def test_cli_reassignment_preview_preserves_gates_and_state(
        world, monkeypatch, capsys, reassign, screen, governor_refusal, expected, marker):
    """A preview must triage the same ownership override as the dispatch plan."""
    from shantytown import cli

    crew, trk = world
    trk.update("item-1", assignee="alan")
    trk.updates = 0
    panes = NullPanes(screen=screen)
    dispatcher = Dispatcher(FilesRegistry(crew), trk, panes,
                            governor=lambda item, agent: governor_refusal)
    monkeypatch.setattr(cli, "_wire", lambda args: dispatcher)
    root = crew.parent
    before = {p.relative_to(root): p.read_bytes()
              for directory in (crew, root / "items")
              for p in directory.rglob("*") if p.is_file()}
    args = ["--root", str(root), "--backend", "files", "--registry", "files",
            "go", "item-1", "ellie", "--dry-run", "--no-graph-context", "fixture"]
    if reassign:
        args.append("--reassign")

    assert cli.main(args) == expected
    output = capsys.readouterr()
    assert marker in output.out + output.err
    assert trk.updates == 0
    assert panes.sent == []
    assert trk.get("item-1").assignee == "alan"
    assert before == {p.relative_to(root): p.read_bytes()
                      for directory in (crew, root / "items")
                      for p in directory.rglob("*") if p.is_file()}
    # Existing preview telemetry is allowed; it must not claim real delivery.
    audit = root / "logs" / "graph-adoption.jsonl"
    if audit.exists():
        assert all(json.loads(line)["dry_run"] for line in audit.read_text().splitlines())
