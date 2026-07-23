"""IdleFleetAlerter — the NEGLECTED / idle-fleet push (internal-ref).

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
                        lambda *a, **k: ["kelly", "weaver"])
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
                        lambda *a, **k: ["kelly", "weaver"])
    mk = lambda: IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY)
    assert sorted(mk().sweep(reg.all())) == ["kelly", "weaver"]   # first: push
    assert mk().sweep(reg.all()) == []                            # same set: silent
    assert mk().sweep(reg.all()) == []
    assert len(panes.sent) == 1, "a still-idle fleet was re-spammed"


def test_a_newly_idle_agent_re_alerts(tmp_path, monkeypatch):
    reg, panes = _world(tmp_path)
    seq = [["kelly"], ["kelly", "weaver"]]        # weaver becomes idle later
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers",
                        lambda *a, **k: seq[0])
    a1 = IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY)
    assert a1.sweep(reg.all()) == ["kelly"]
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers",
                        lambda *a, **k: seq[1])
    a2 = IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY)
    assert a2.sweep(reg.all()) == ["weaver"], "the newly-idle agent must alert"
    assert len(panes.sent) == 2


def test_a_worker_that_leaves_free_is_re_armed(tmp_path, monkeypatch):
    reg, panes = _world(tmp_path)
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a, **k: ["kelly"])
    assert IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY).sweep(reg.all()) == ["kelly"]
    # kelly gets dispatched (no longer free) -> ledger forgets it.
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a, **k: [])
    assert IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY).sweep(reg.all()) == []
    # kelly goes idle AGAIN -> fresh episode, alerts again.
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a, **k: ["kelly"])
    assert IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY).sweep(reg.all()) == ["kelly"]


# --- no false alert: free but no work, or dark-only -------------------------

def test_free_workers_but_no_dispatchable_work_does_not_alert(tmp_path, monkeypatch):
    reg, panes = _world(tmp_path)
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a, **k: ["kelly"])
    # ready beads all dark-assigned -> dispatchable() returns [] -> not neglect.
    a = IdleFleetAlerter(tmp_path, reg, panes, None,
                         bd_ready=lambda: [{"id": "x", "assignee": "crew/arnold"}])
    assert a.sweep(reg.all()) == []
    assert panes.sent == []
    # and it did NOT record kelly, so when work appears the alert fires.
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a, **k: ["kelly"])
    a2 = IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY)
    assert a2.sweep(reg.all()) == ["kelly"]


def test_no_free_workers_is_silent(tmp_path, monkeypatch):
    reg, panes = _world(tmp_path)
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a, **k: [])
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
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a, **k: ["kelly"])
    def bd_boom():
        raise RuntimeError("bd down")
    a = IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=bd_boom)
    assert a.sweep(reg.all()) == []               # fail-open, no push
    assert panes.sent == []
    # bd recovers -> kelly (never recorded) alerts.
    a2 = IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY)
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a, **k: ["kelly"])
    assert a2.sweep(reg.all()) == ["kelly"]


def test_an_unreachable_coordinator_is_not_recorded(tmp_path, monkeypatch):
    # no admin pane live -> push_to_admin returns None -> not recorded, retried.
    reg, panes = _world(tmp_path, admin_pane="p-down")
    panes._live.discard("p-down")
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a, **k: ["kelly"])
    a = IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY)
    assert a.sweep(reg.all()) == []
    # admin comes back -> retry fires.
    panes._live.add("p-down")
    a2 = IdleFleetAlerter(tmp_path, reg, panes, None, bd_ready=lambda: READY)
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers", lambda *a, **k: ["kelly"])
    assert a2.sweep(reg.all()) == ["kelly"]


def test_the_message_names_who_is_free_and_what_is_ready():
    msg = _idle_fleet_message(["kelly", "weaver"], ["weaver"], [("aegis-9", "fix the thing")])
    assert "kelly" in msg and "weaver" in msg and "aegis-9" in msg
    assert "newly idle: weaver" in msg
    assert "DISPATCH" in msg and "RULE ZERO" in msg


# --- the haul groundwork (internal-ref): assigned = self-feeding -------------

def _hauling_world(tmp_path, monkeypatch, in_progress=None, context_k=None,
                   claims=None, ready=None):
    """One idle worker with an ASSIGNED ready bead, one admin to (not) alert.

    in_progress may be a list (the bd answer) or an Exception instance (bd
    unreadable — the feed-nobody fail-safe)."""
    reg = _Reg([Agent(name="sattler", role="administrator", pane="p-admin"),
                Agent(name="billy", role="worker", pane="p-billy")])
    panes = _Panes({"p-admin", "p-billy"})
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers",
                        lambda *a, **k: ["billy"])
    monkeypatch.setattr("shantytown.feed_check.bd_cwd", lambda reg: None)
    if claims is not None:
        monkeypatch.setattr("shantytown.feed_check.bd_claim",
                            lambda cwd, nid: claims.append(nid))
    if ready is None:
        ready = [{"id": "aegis-9", "title": "queued work",
                  "assignee": "beads_aegis/crew/billy"}]

    def bd_in_progress(cwd):
        if isinstance(in_progress, Exception):
            raise in_progress
        return in_progress or []

    return IdleFleetAlerter(tmp_path, reg, panes, runtime=None,
                            bd_ready=lambda: ready,
                            bd_in_progress=bd_in_progress,
                            context_k=(lambda w: context_k),
                            log=lambda m: None), panes


def test_an_already_idle_hauler_is_FED_its_next_bead_not_the_coordinator(tmp_path, monkeypatch):
    """The already-idle gap, closed: an idle worker never stops again on its
    own, so tend is the second advance trigger — it FEEDS the actual next bead
    (claimed, named, same voice as the stop hook), and the coordinator hears
    NOTHING."""
    claims = []
    alerter, panes = _hauling_world(tmp_path, monkeypatch, claims=claims)
    assert alerter.sweep([]) == ["billy"]
    targets = [p for p, _ in panes.sent]
    assert "p-billy" in targets, "the worker must be fed"
    assert "p-admin" not in targets, "the coordinator must hear nothing"
    (_, msg), = [x for x in panes.sent if x[0] == "p-billy"]
    assert "HAUL" in msg and "aegis-9" in msg and "bd show aegis-9" in msg
    assert claims == ["aegis-9"], "the fed bead is claimed in_progress"


def test_the_feed_is_once_per_idle_episode(tmp_path, monkeypatch):
    alerter, panes = _hauling_world(tmp_path, monkeypatch, claims=[])
    alerter.sweep([])
    alerter.sweep([])
    alerter.sweep([])
    assert len(panes.sent) == 1, "a 30s heartbeat must not re-spam the worker"


def test_an_open_anchor_does_NOT_block_the_feed(tmp_path, monkeypatch):
    """The u13t wedge, closed (INVERTS the old active-anchor guard): the pane
    is IDLE, so an in_progress anchor is not being worked NOW — it is pending
    human review, parked on a HITL blocker, or a forgotten close. The old
    blanket skip stranded the queue forever (worker never re-stops, tend logged
    "not fed" every pass, coordinator got pinged — the dominant Rule-Zero toil
    source, 3x in one session). Drain safety lives in the once-per-idle-episode
    dedup, not in this guard."""
    claims = []
    alerter, panes = _hauling_world(
        tmp_path, monkeypatch, claims=claims,
        in_progress=[{"id": "aegis-1", "assignee": "billy"}])
    assert alerter.sweep([]) == ["billy"], "idle + open anchor must still feed"
    (_, msg), = [x for x in panes.sent if x[0] == "p-billy"]
    assert "aegis-9" in msg and claims == ["aegis-9"]


def test_the_fed_bead_is_never_the_open_anchor_itself(tmp_path, monkeypatch):
    """If the open anchor somehow also appears in the ready queue (bd edge),
    the feed picks the NEXT bead — never re-feeds what the worker already
    holds. Exactly one claim."""
    claims = []
    ready = [{"id": "aegis-1", "title": "the open anchor",
              "assignee": "beads_aegis/crew/billy"},
             {"id": "aegis-9", "title": "queued work",
              "assignee": "beads_aegis/crew/billy"},
             {"id": "aegis-10", "title": "more queued work",
              "assignee": "beads_aegis/crew/billy"}]
    alerter, panes = _hauling_world(
        tmp_path, monkeypatch, claims=claims, ready=ready,
        in_progress=[{"id": "aegis-1", "assignee": "billy"}])
    assert alerter.sweep([]) == ["billy"]
    (_, msg), = [x for x in panes.sent if x[0] == "p-billy"]
    assert "aegis-9" in msg, "feeds the first bead that is not the anchor"
    assert claims == ["aegis-9"], "claims exactly one, and not the anchor"


def test_a_queue_that_is_only_the_open_anchor_is_not_fed(tmp_path, monkeypatch):
    """Nothing to feed that the worker does not already hold -> no feed, no
    claim, no coordinator ping."""
    claims = []
    ready = [{"id": "aegis-1", "title": "the open anchor",
              "assignee": "beads_aegis/crew/billy"}]
    alerter, panes = _hauling_world(
        tmp_path, monkeypatch, claims=claims, ready=ready,
        in_progress=[{"id": "aegis-1", "assignee": "billy"}])
    assert alerter.sweep([]) == []
    assert panes.sent == [] and claims == []


def test_an_unreadable_in_progress_set_feeds_nobody(tmp_path, monkeypatch):
    """bd unreadable -> cannot tell which beads the worker holds -> the
    fail-safe survives the u13t inversion: feed NOBODY rather than guess."""
    claims = []
    alerter, panes = _hauling_world(
        tmp_path, monkeypatch, claims=claims,
        in_progress=RuntimeError("bd down"))
    assert alerter.sweep([]) == []
    assert panes.sent == [] and claims == []


def test_the_feed_with_open_anchor_is_still_once_per_idle_episode(tmp_path, monkeypatch):
    """The drain-safety claim the inversion rests on, pinned: with the anchor
    guard gone, the newly-idle dedup alone must bound tend to ONE feed per
    idle episode."""
    alerter, panes = _hauling_world(
        tmp_path, monkeypatch, claims=[],
        in_progress=[{"id": "aegis-1", "assignee": "billy"}])
    alerter.sweep([])
    alerter.sweep([])
    alerter.sweep([])
    assert len(panes.sent) == 1, "one feed per idle episode, anchor or not"


def test_past_the_handoff_line_tend_instructs_the_reset_not_food(tmp_path, monkeypatch):
    claims = []
    alerter, panes = _hauling_world(tmp_path, monkeypatch, claims=claims,
                                    context_k=650.0)
    assert alerter.sweep([]) == ["billy"]
    (_, msg), = [x for x in panes.sent if x[0] == "p-billy"]
    assert "HANDOFF" in msg and "650" in msg
    assert "aegis-9" not in msg and claims == [], "past the line, nothing is fed"


