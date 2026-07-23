"""notify — a blocked worker is PUSHED to the coordinator, on its own (internal-ref).

The bug was detection-without-delivery: the tier could classify a `waiting`
worker but only DELIVERED that on the coordinator's OWN stop, so kelly sat blocked
unseen and weaver parked for hours. These tests pin the mechanical acceptance —
block a worker, DO NOT touch the coordinator, the coordinator's pane receives it —
and the dedup invariant that keeps a heartbeat from becoming a spam channel.
"""
from __future__ import annotations
import json
from pathlib import Path

from shantytown import triage
from shantytown.notify import Notifier, blocked_workers, wake_recipient
from shantytown.protocols import Agent


# A pane with a BLOCKING picker up — what a `waiting` worker actually looks like.
PICKER = ("Do you want to proceed?\n"
          "❯ 1. Yes\n  2. No\n"
          "  Enter to select · Esc to cancel")
IDLE = "❯ \n  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"


class _Runtime:
    name = "fake"

    def shows_ready_ui(self, screen):
        return "shift+tab to cycle" in screen

    def awaiting_answer(self, screen):
        return "Enter to select" in screen


class _Panes:
    """Per-pane screens; records every send as (pane, text) — the push channel."""

    def __init__(self, screens):
        self._screens = screens
        self.sent = []

    def exists(self, pane):
        return pane in self._screens

    def capture(self, pane, history=0, attrs=False):
        return self._screens.get(pane, "")

    def send(self, pane, text):
        self.sent.append((pane, text))


class _Reg:
    def __init__(self, cards):
        self._c = {a.name: a for a in cards}

    def get(self, name):
        return self._c[name]

    def all(self):
        return list(self._c.values())


def _world(screens, coord_pane="p-sattler"):
    reg = _Reg([
        Agent(name="kelly", role="worker", reports_to="sattler", pane="p-kelly"),
        Agent(name="sattler", role="administrator", pane=coord_pane),
    ])
    return reg, _Panes(screens), _Runtime()


# --- detection: only actionable blocks, only workers ------------------------

def test_a_picker_blocked_worker_is_detected():
    _reg, panes, rt = _world({"p-kelly": PICKER, "p-sattler": IDLE})
    assert blocked_workers([_reg.get("kelly"), _reg.get("sattler")], panes, rt) == \
        [("kelly", triage.WAITING)]


def test_an_idle_worker_is_not_blocked():
    _reg, panes, rt = _world({"p-kelly": IDLE, "p-sattler": IDLE})
    assert blocked_workers([_reg.get("kelly")], panes, rt) == []


# --- the mechanical acceptance: the coordinator pane receives it -------------

def test_a_block_pushes_to_the_coordinator_without_touching_it(tmp_path):
    reg, panes, rt = _world({"p-kelly": PICKER, "p-sattler": IDLE})
    woke = Notifier(tmp_path, reg, panes).sweep(reg.all(), rt)

    assert woke == ["kelly"]
    # The coordinator's pane — and ONLY it — got the push. Nothing was sent to the
    # blocked worker; the coordinator was never asked to sweep.
    assert len(panes.sent) == 1
    pane, text = panes.sent[0]
    assert pane == "p-sattler"
    assert "kelly" in text and "BLOCKED" in text and "st log kelly" in text


def test_the_push_goes_to_the_route_stop_recipient(tmp_path):
    # A worker under a LEAD is woken to the LEAD, not the admin — the same place
    # its stop events route, so alerts and stops agree about who is watching.
    reg = _Reg([
        Agent(name="ellie", role="worker", reports_to="maldoon", pane="p-ellie"),
        Agent(name="maldoon", role="lead", reports_to="sattler", pane="p-maldoon"),
        Agent(name="sattler", role="administrator", pane="p-sattler"),
    ])
    panes = _Panes({"p-ellie": PICKER, "p-maldoon": IDLE, "p-sattler": IDLE})
    Notifier(tmp_path, reg, panes).sweep(reg.all(), _Runtime())
    assert [p for p, _ in panes.sent] == ["p-maldoon"]


# --- dedup: once per episode, re-armed on recovery --------------------------

def test_a_still_blocked_worker_is_not_re_notified(tmp_path):
    reg, panes, rt = _world({"p-kelly": PICKER, "p-sattler": IDLE})
    n = Notifier(tmp_path, reg, panes)

    assert n.sweep(reg.all(), rt) == ["kelly"]      # first sweep: pushed
    assert n.sweep(reg.all(), rt) == []             # still blocked: silent
    assert n.sweep(reg.all(), rt) == []
    assert len(panes.sent) == 1, "a heartbeat re-spammed a still-blocked worker"


def test_dedup_survives_the_process_restarting(tmp_path):
    reg, panes, rt = _world({"p-kelly": PICKER, "p-sattler": IDLE})
    assert Notifier(tmp_path, reg, panes).sweep(reg.all(), rt) == ["kelly"]
    # A brand-new Notifier (the sweeper restarted) must read the durable ledger
    # and stay quiet — otherwise every restart re-spams.
    assert Notifier(tmp_path, reg, panes).sweep(reg.all(), rt) == []


def test_recovery_re_arms_the_notification(tmp_path):
    reg = _Reg([
        Agent(name="kelly", role="worker", reports_to="sattler", pane="p-kelly"),
        Agent(name="sattler", role="administrator", pane="p-sattler"),
    ])
    blocked = _Panes({"p-kelly": PICKER, "p-sattler": IDLE})
    n = Notifier(tmp_path, reg, blocked)
    assert n.sweep(reg.all(), _Runtime()) == ["kelly"]

    # kelly answers and goes idle — the ledger must forget it.
    recovered = _Panes({"p-kelly": IDLE, "p-sattler": IDLE})
    n2 = Notifier(tmp_path, reg, recovered)
    assert n2.sweep(reg.all(), _Runtime()) == []

    # kelly blocks AGAIN — a fresh episode, so it notifies again.
    n3 = Notifier(tmp_path, reg, blocked)
    assert n3.sweep(reg.all(), _Runtime()) == ["kelly"]


# --- a failed push must NOT be recorded as delivered ------------------------

def test_an_unreachable_coordinator_is_not_swallowed(tmp_path):
    # coordinator pane is DOWN (not in the panes map).
    reg = _Reg([
        Agent(name="kelly", role="worker", reports_to="sattler", pane="p-kelly"),
        Agent(name="sattler", role="administrator", pane="p-sattler-down"),
    ])
    panes = _Panes({"p-kelly": PICKER})            # no p-sattler-down
    logs = []
    n = Notifier(tmp_path, reg, panes, log=logs.append)

    assert n.sweep(reg.all(), _Runtime()) == []    # nothing DELIVERED
    assert any("unreachable" in m for m in logs)
    # and it stays PENDING: when the coordinator comes back, the retry fires.
    panes._screens["p-sattler-down"] = IDLE
    assert n.sweep(reg.all(), _Runtime()) == ["kelly"]


def test_wake_recipient_returns_None_when_there_is_nowhere_to_send(tmp_path):
    reg = _Reg([Agent(name="lonely", role="worker", reports_to=None, pane="p-x")])
    panes = _Panes({"p-x": PICKER})
    # no administrator exists -> route_stop raises -> None, never a false success
    assert wake_recipient(reg, panes, "lonely", "msg") is None
    assert panes.sent == []


# --- the ENOSPC night (internal-ref): torn ledgers and a dead supervisor -------

def test_a_zero_byte_ledger_reads_as_empty_not_a_crash(tmp_path):
    """The disk-full write left a 0-byte blocked.json. A later load must treat
    it as 'no ledger', never die on it — the drain's ev-172 lesson, ledger
    edition."""
    from shantytown.notify import Notifier
    path = tmp_path / "notify" / "blocked.json"
    path.parent.mkdir(parents=True)
    path.write_text("")
    n = Notifier(tmp_path, None, None)
    assert n._load() == {}


def test_ledger_saves_are_atomic_no_torn_file_is_ever_final(tmp_path, monkeypatch):
    """_save goes through tmp + os.replace: a writer killed mid-write can leave
    only a .tmp corpse — the FINAL name always holds valid JSON. Proven by
    failing the replace and checking the final file was never touched."""
    import os as _os
    import json as _json
    from shantytown.notify import Notifier
    n = Notifier(tmp_path, None, None)
    n._save({"a": "blocked"})
    assert _json.loads(n.path.read_text()) == {"a": "blocked"}

    def boom(src, dst):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(_os, "replace", boom)
    try:
        n._save({"b": "torn"})
    except OSError:
        pass
    # The final file still holds the PREVIOUS valid state — never 0 bytes.
    assert _json.loads(n.path.read_text()) == {"a": "blocked"}
