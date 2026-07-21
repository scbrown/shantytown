"""IdleFleetAlerter — the NEGLECTED / idle-fleet push (aegis-nk0e).

The soft half of Rule Zero: when free feedable workers and dispatchable beads
coexist, PUSH the coordinator. The coordinator stalled tonight — handled one
question and stopped while nine agents sat idle with a full ready queue — which is
the same invisible failure w0kk fixed for blocked workers. These tests pin the
push, the dedup (still-idle does not re-spam; newly-idle does), the dark
exclusion (reused from feed_check), and FAIL-OPEN.
"""
from __future__ import annotations
import json

from shantytown import notify
from shantytown.notify import IdleFleetAlerter, _idle_fleet_message
from shantytown.protocols import Agent


class _Reg:
    def __init__(self, agents):
        self._a = {x.name: x for x in agents}

    def all(self):
        return list(self._a.values())

    def get(self, name):
        return self._a[name]


class _Panes:
    def __init__(self, live):
        self._live = set(live)
        self.sent = []

    def exists(self, pane):
        return pane in self._live

    def send(self, pane, text):
        self.sent.append((pane, text))


def _world(tmp_path, admin_pane="p-sattler"):
    reg = _Reg([
        Agent(name="sattler", role="administrator", pane=admin_pane),
        Agent(name="weaver", role="worker", reports_to="sattler", pane="p-weaver"),
        Agent(name="kelly", role="worker", reports_to="sattler", pane="p-kelly"),
    ])
    panes = _Panes({admin_pane, "p-weaver", "p-kelly"})
    return reg, panes


READY = [{"id": "aegis-9", "title": "fix the thing"}]


def _alerter(tmp_path, reg, panes, free, ready=READY):
    # free_feedable_workers and _bd_ready are the two seams reused from feed_check;
    # stub them so the test drives dedup/push without tmux or bd.
    return IdleFleetAlerter(
        tmp_path, reg, panes, runtime=None,
        bd_ready=lambda: ready,
        log=lambda m: None), free


# --- the push, to the coordinator -------------------------------------------

def test_pushes_the_coordinator_when_free_and_work_coexist(tmp_path, monkeypatch):
    reg, panes = _world(tmp_path)
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers",
                        lambda *a: ["kelly", "weaver"])
    a = IdleFleetAlerter(tmp_path, reg, panes, runtime=None,
                         bd_ready=lambda: READY)
    newly = a.sweep(reg.all())

    assert sorted(newly) == ["kelly", "weaver"]
    assert len(panes.sent) == 1
    pane, text = panes.sent[0]
    assert pane == "p-sattler"                    # the coordinator, not a worker
    assert "kelly" in text and "weaver" in text and "aegis-9" in text
    assert "DISPATCH" in text


# --- dedup: still-idle silent, newly-idle alerts ----------------------------

def test_a_still_idle_fleet_does_not_re_spam(tmp_path, monkeypatch):
    reg, panes = _world(tmp_path)
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers",
                        lambda *a: ["kelly", "weaver"])
    mk = lambda: IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY)
    assert sorted(mk().sweep(reg.all())) == ["kelly", "weaver"]   # first: push
    assert mk().sweep(reg.all()) == []                            # same set: silent
    assert mk().sweep(reg.all()) == []
    assert len(panes.sent) == 1, "a still-idle fleet was re-spammed"


def test_a_newly_idle_agent_re_alerts(tmp_path, monkeypatch):
    reg, panes = _world(tmp_path)
    seq = [["kelly"], ["kelly", "weaver"]]        # weaver becomes idle later
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers",
                        lambda *a: seq[0])
    a1 = IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY)
    assert a1.sweep(reg.all()) == ["kelly"]
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers",
                        lambda *a: seq[1])
    a2 = IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY)
    assert a2.sweep(reg.all()) == ["weaver"], "the newly-idle agent must alert"
    assert len(panes.sent) == 2


def test_a_worker_that_leaves_free_is_re_armed(tmp_path, monkeypatch):
    reg, panes = _world(tmp_path)
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a: ["kelly"])
    assert IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY).sweep(reg.all()) == ["kelly"]
    # kelly gets dispatched (no longer free) -> ledger forgets it.
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a: [])
    assert IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY).sweep(reg.all()) == []
    # kelly goes idle AGAIN -> fresh episode, alerts again.
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a: ["kelly"])
    assert IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY).sweep(reg.all()) == ["kelly"]


# --- no false alert: free but no work, or dark-only -------------------------

def test_free_workers_but_no_dispatchable_work_does_not_alert(tmp_path, monkeypatch):
    reg, panes = _world(tmp_path)
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a: ["kelly"])
    # ready beads all dark-assigned -> dispatchable() returns [] -> not neglect.
    a = IdleFleetAlerter(tmp_path, reg, panes, None,
                         bd_ready=lambda: [{"id": "x", "assignee": "crew/arnold"}])
    assert a.sweep(reg.all()) == []
    assert panes.sent == []
    # and it did NOT record kelly, so when work appears the alert fires.
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a: ["kelly"])
    a2 = IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY)
    assert a2.sweep(reg.all()) == ["kelly"]


def test_no_free_workers_is_silent(tmp_path, monkeypatch):
    reg, panes = _world(tmp_path)
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a: [])
    a = IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY)
    assert a.sweep(reg.all()) == [] and panes.sent == []


# --- FAIL OPEN --------------------------------------------------------------

def test_a_broken_detector_stays_quiet(tmp_path, monkeypatch):
    reg, panes = _world(tmp_path)
    def boom(*a):
        raise RuntimeError("tmux gone")
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", boom)
    a = IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY)
    assert a.sweep(reg.all()) == [] and panes.sent == []


def test_a_bd_hiccup_does_not_alert_and_leaves_it_pending(tmp_path, monkeypatch):
    reg, panes = _world(tmp_path)
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a: ["kelly"])
    def bd_boom():
        raise RuntimeError("bd down")
    a = IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=bd_boom)
    assert a.sweep(reg.all()) == []               # fail-open, no push
    assert panes.sent == []
    # bd recovers -> kelly (never recorded) alerts.
    a2 = IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY)
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a: ["kelly"])
    assert a2.sweep(reg.all()) == ["kelly"]


def test_an_unreachable_coordinator_is_not_recorded(tmp_path, monkeypatch):
    # no admin pane live -> push_to_admin returns None -> not recorded, retried.
    reg, panes = _world(tmp_path, admin_pane="p-down")
    panes._live.discard("p-down")
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a: ["kelly"])
    a = IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY)
    assert a.sweep(reg.all()) == []
    # admin comes back -> retry fires.
    panes._live.add("p-down")
    a2 = IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY)
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a: ["kelly"])
    assert a2.sweep(reg.all()) == ["kelly"]


def test_the_message_names_who_is_free_and_what_is_ready():
    msg = _idle_fleet_message(["kelly", "weaver"], ["weaver"], [("aegis-9", "fix the thing")])
    assert "kelly" in msg and "weaver" in msg and "aegis-9" in msg
    assert "newly idle: weaver" in msg
    assert "DISPATCH" in msg and "RULE ZERO" in msg
