"""feed_check — the administrator's Rule Zero HARD GATE (aegis-hfta).

BLOCK the coordinator's own stop while free FEEDABLE workers AND dispatchable beads
coexist; ALLOW (fail open) on any error, when nobody is free, or when there is no
dispatchable work. These tests pin every branch of that, plus the two constraints
that keep it from false-trapping: dark workers are not "free", and dark-assigned
beads are not "dispatchable".
"""
from __future__ import annotations
import json

import pytest

from shantytown import feed_check
from shantytown.protocols import Agent


# --- dispatchable: unassigned OR assigned-to-a-free-worker ------------------

def test_unassigned_ready_beads_are_dispatchable():
    ready = [{"id": "aegis-1", "title": "a"}, {"id": "aegis-2", "title": "b"}]
    got = feed_check.dispatchable({"weaver"}, ready)
    assert [b[0] for b in got] == ["aegis-1", "aegis-2"]


def test_a_bead_assigned_to_a_dark_agent_is_NOT_dispatchable():
    # arnold is dark (not in the free set): its bead is stuck, not feedable.
    ready = [{"id": "aegis-1", "title": "a", "assignee": "beads_aegis/crew/arnold"}]
    assert feed_check.dispatchable({"weaver"}, ready) == []


def test_a_bead_assigned_to_a_free_worker_is_dispatchable():
    ready = [{"id": "aegis-1", "title": "a", "assignee": "weaver"}]
    assert [b[0] for b in feed_check.dispatchable({"weaver"}, ready)] == ["aegis-1"]


def test_a_board_of_all_dark_assigned_beads_is_not_dispatchable():
    ready = [{"id": "aegis-1", "assignee": "crew/arnold"},
             {"id": "aegis-2", "assignee": "crew/ellie"}]
    assert feed_check.dispatchable({"weaver"}, ready) == []


# --- free = feedable: dark workers excluded, unreadable excluded ------------

class _Runtime:
    def shows_ready_ui(self, screen):
        return "shift+tab" in screen

    def awaiting_answer(self, screen):
        return "Enter to select" in screen


IDLE = "❯ \n  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
BUSY = "✻ Envisioning… (12s · esc to interrupt)"
SEND_CMDLINE = "claude --settings /s.json"     # carries a stop_event send hook


class _Panes:
    def __init__(self, screens, cmdlines):
        self._screens = screens
        self._cmdlines = cmdlines

    def exists(self, pane):
        return pane in self._screens

    def capture(self, pane, history=0, attrs=False):
        return self._screens.get(pane, "")

    def cmdline(self, pane):
        return self._cmdlines.get(pane)


class _Reg:
    def __init__(self, agents):
        self._a = agents

    def all(self):
        return self._a


def _send_settings(tmp_path):
    p = tmp_path / "worker.settings.json"
    p.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "python -m shantytown.stop_event send"}]}]}}))
    return p


def test_an_idle_wired_worker_is_free(tmp_path):
    settings = _send_settings(tmp_path)
    reg = _Reg([Agent(name="weaver", role="worker", pane="shanty-weaver")])
    panes = _Panes({"shanty-weaver": IDLE},
                   {"shanty-weaver": f"claude --settings {settings}"})
    assert feed_check.free_feedable_workers(reg, panes, _Runtime()) == ["weaver"]


def test_a_dark_worker_is_not_free(tmp_path):
    # no --settings on the cmdline -> no send wiring -> dark -> not free.
    reg = _Reg([Agent(name="arnold", role="worker", pane="aegis-crew-arnold")])
    panes = _Panes({"aegis-crew-arnold": IDLE},
                   {"aegis-crew-arnold": "claude --no-such-flag"})
    assert feed_check.free_feedable_workers(reg, panes, _Runtime()) == []


def test_a_busy_worker_is_not_free(tmp_path):
    settings = _send_settings(tmp_path)
    reg = _Reg([Agent(name="tim", role="worker", pane="shanty-tim")])
    panes = _Panes({"shanty-tim": BUSY}, {"shanty-tim": f"claude --settings {settings}"})
    assert feed_check.free_feedable_workers(reg, panes, _Runtime()) == []


def test_unreadable_wiring_excludes_the_worker(tmp_path):
    # cmdline None -> wiring None -> not feedable (the safe direction).
    reg = _Reg([Agent(name="x", role="worker", pane="shanty-x")])
    panes = _Panes({"shanty-x": IDLE}, {})       # no cmdline
    assert feed_check.free_feedable_workers(reg, panes, _Runtime()) == []


# --- main(): block only when both hold; allow (fail-open) otherwise ---------

def _wire_main(monkeypatch, free, ready_beads=None, bd_raises=False):
    monkeypatch.setattr(feed_check, "free_feedable_workers", lambda *a: free)
    if bd_raises:
        def boom():
            raise RuntimeError("bd unreachable")
        monkeypatch.setattr(feed_check, "_bd_ready", boom)
    else:
        monkeypatch.setattr(feed_check, "_bd_ready", lambda: ready_beads or [])
    # neutralise the store/tmux setup so main reaches the injected functions.
    import shantytown.files as f
    monkeypatch.setattr(f, "FilesRegistry", lambda *a, **k: object())
    monkeypatch.setattr("shantytown.tmux.Tmux", lambda *a, **k: object())
    monkeypatch.setattr("shantytown.tmux.declared_socket", lambda *a: None)
    monkeypatch.setattr("shantytown.runtime.ClaudeRuntime", lambda *a, **k: object())


def test_blocks_when_free_and_dispatchable_both_exist(monkeypatch, capsys):
    _wire_main(monkeypatch, free=["weaver"],
               ready_beads=[{"id": "aegis-9", "title": "fix the thing"}])
    rc = feed_check.main(["--root", "/x"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block"
    assert "weaver" in out["reason"] and "aegis-9" in out["reason"]
    assert "Rule Zero".upper() in out["reason"].upper()


def test_allows_when_nobody_is_free(monkeypatch, capsys):
    _wire_main(monkeypatch, free=[], ready_beads=[{"id": "aegis-9"}])
    assert feed_check.main(["--root", "/x"]) == 0
    assert capsys.readouterr().out == "", "no free workers -> allow, print nothing"


def test_allows_when_no_dispatchable_work(monkeypatch, capsys):
    # free workers, but the only ready bead is dark-assigned -> not dispatchable.
    _wire_main(monkeypatch, free=["weaver"],
               ready_beads=[{"id": "aegis-9", "assignee": "crew/arnold"}])
    assert feed_check.main(["--root", "/x"]) == 0
    assert capsys.readouterr().out == "", "no dispatchable work -> allow"


def test_FAILS_OPEN_when_bd_is_unreachable(monkeypatch, capsys):
    # THE critical invariant: a bd hiccup must never trap the coordinator.
    _wire_main(monkeypatch, free=["weaver"], bd_raises=True)
    assert feed_check.main(["--root", "/x"]) == 0
    assert capsys.readouterr().out == "", "bd error -> ALLOW the stop, never block"


def test_FAILS_OPEN_when_the_registry_setup_raises(monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("no store")
    monkeypatch.setattr("shantytown.files.FilesRegistry", boom)
    assert feed_check.main(["--root", "/x"]) == 0
    assert capsys.readouterr().out == "", "any error -> allow"


def test_self_terminates_when_free_hits_zero(monkeypatch, capsys):
    # The self-termination proof: same store, but free drops to 0 (all dispatched)
    # -> the stop is now ALLOWED. It terminates on the fleet being fed, not a counter.
    _wire_main(monkeypatch, free=[], ready_beads=[{"id": "aegis-9"}])
    assert feed_check.main(["--root", "/x"]) == 0
    assert capsys.readouterr().out == ""
