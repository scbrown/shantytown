"""The tests ARE the design. Two of them are the reason this repo exists.

- test_dry_run_writes_nothing:  --dry-run from commit one. A real sling was
  fired as a diagnostic during design and hooked an agent with work nobody
  meant to assign.
- test_budget_*:  count the calls, don't hold a stopwatch. gt sling would have
  passed a "feels fine" check on a quiet night.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown.dispatch import Dispatcher
from shantytown.files import FilesRegistry, FilesTracker
from shantytown.tmux import NullPanes


class CountingTracker(FilesTracker):
    """Wraps the real tracker and counts. The budget is a test, not a vibe."""

    def __init__(self, root):
        super().__init__(root)
        self.gets = 0
        self.updates = 0

    def get(self, item_id):
        self.gets += 1
        return super().get(item_id)

    def update(self, item_id, **fields):
        self.updates += 1
        return super().update(item_id, **fields)


@pytest.fixture
def world(tmp_path: Path):
    reg_dir = tmp_path / "crew"; reg_dir.mkdir()
    (reg_dir / "ellie.json").write_text(json.dumps(
        {"role": "worker", "reports_to": "malcolm", "pane": "%5"}))
    (reg_dir / "nopane.json").write_text(json.dumps({"role": "worker"}))
    trk_dir = tmp_path / "items"
    tracker = CountingTracker(trk_dir)
    tracker.update("item-1", title="Restore the den service", status="open")
    tracker.updates = 0  # setup doesn't count against the budget
    panes = NullPanes()
    return Dispatcher(FilesRegistry(reg_dir), tracker, panes), tracker, panes


def test_dry_run_writes_nothing(world):
    """--dry-run is non-negotiable and it is FIRST, not last."""
    d, tracker, panes = world
    before = tracker.get("item-1"); tracker.gets = 0

    plan = d.go("item-1", "ellie", dry_run=True)

    assert tracker.updates == 0, "dry-run wrote to the tracker"
    assert panes.sent == [], "dry-run sent keys"
    assert tracker.get("item-1") == before, "dry-run mutated state"
    assert "would:" in plan.render()


def test_dry_run_is_idempotent(world):
    """Run it twice. Ask the question without the consequence."""
    d, tracker, _ = world
    d.go("item-1", "ellie", dry_run=True)
    d.go("item-1", "ellie", dry_run=True)
    assert tracker.updates == 0


def test_dispatch_actually_dispatches(world):
    """vision.md item 1: dispatch a real item to a real agent, no Gas Town."""
    d, tracker, panes = world
    d.go("item-1", "ellie")
    after = tracker.get("item-1")
    assert after.status == "in_progress"
    assert after.assignee == "ellie"
    assert len(panes.sent) == 1
    pane, text = panes.sent[0]
    assert pane == "%5"
    assert "item-1" in text


def test_budget_counts_not_stopwatch(world):
    """docs/cli.md: tracker calls <= 2, sends 1, waits for ack 0.

    gt sling --dry-run: 51.54s, 63 Dolt connections, and it writes NOTHING.
    A stopwatch would have passed that on a quiet night. Count the calls.
    """
    d, tracker, panes = world
    tracker.gets = 0; tracker.updates = 0

    d.go("item-1", "ellie")

    assert tracker.gets + tracker.updates <= 2, (
        f"budget blown: {tracker.gets} gets + {tracker.updates} updates > 2"
    )
    assert len(panes.sent) == 1


def test_budget_dry_run_is_read_only(world):
    d, tracker, panes = world
    tracker.gets = 0; tracker.updates = 0
    d.go("item-1", "ellie", dry_run=True)
    assert tracker.updates == 0
    assert tracker.gets <= 1


def test_refuses_before_writing_when_no_pane(world):
    """Refusal is a precondition failure and it happens BEFORE any write."""
    d, tracker, panes = world
    tracker.updates = 0
    with pytest.raises(LookupError, match="no pane"):
        d.go("item-1", "nopane")
    assert tracker.updates == 0, "refused AND still wrote — half-dispatch"
    assert panes.sent == []


def test_refuses_unknown_agent(world):
    d, tracker, _ = world
    tracker.updates = 0
    with pytest.raises(LookupError, match="no such agent"):
        d.go("item-1", "ghost")
    assert tracker.updates == 0


# --- socket: bare tmux cannot see a named server, and says "down" instead of erroring ---

def test_tmux_socket_is_threaded_into_every_command():
    """Standing shantytown up on its own host printed `down` for all 8 crew while
    every one was live on a named socket. Bare tmux does not error on a socket
    it cannot see — it returns an empty list, exit 0. A false negative about
    liveness is the worst answer this adapter can give."""
    from shantytown.tmux import Tmux
    t = Tmux(socket="my-socket")
    assert t._cmd("list-panes") == ["tmux", "-L", "my-socket", "list-panes"]
    # -L must precede the subcommand or tmux rejects it
    assert t._cmd("send-keys", "-t", "p")[:4] == ["tmux", "-L", "my-socket", "send-keys"]


def test_tmux_socket_defaults_to_bare_tmux():
    from shantytown.tmux import Tmux
    assert Tmux(socket=None)._cmd("list-panes") == ["tmux", "list-panes"]


def test_tmux_socket_reads_env(monkeypatch):
    from shantytown.tmux import Tmux
    monkeypatch.setenv("SHANTY_TMUX_SOCKET", "sock1")
    assert Tmux()._cmd("ls") == ["tmux", "-L", "sock1", "ls"]


def test_exists_matches_session_names_not_only_pane_ids():
    """Our panes are addressed by session name (crew-ian); #{pane_id} only
    ever yields %N, so a pane_id-only check reports down for every agent."""
    from shantytown.tmux import Tmux
    import subprocess
    t = Tmux()
    out = subprocess.CompletedProcess([], 0, stdout="%1 crew-ian\n%2 other\n", stderr="")
    orig = subprocess.run
    try:
        subprocess.run = lambda *a, **k: out
        assert t.exists("crew-ian") is True
        assert t.exists("crew-nobody") is False
    finally:
        subprocess.run = orig
