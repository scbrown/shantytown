"""quipu_events — the EventSource over Quipu's cursored transaction log.

The load-bearing behaviours: the watermark advances only after a batch is fully
handled; a could-not-look keeps the watermark (never a silent skip); workflows
are routed once (dedup across restarts via the handled set); four-state liveness
(reachable vs quiet vs unreachable). The HTTP is faked (mirrors test_reactor's
_Fake), so nothing here touches a socket.
"""
from __future__ import annotations

import json

import pytest

from shantytown import quipu_events as qe
from shantytown.protocols import Event, EventsUnavailable


class _Fake(qe.QuipuEvents):
    """QuipuEvents with the two HTTP methods replaced by canned data. `txns` is a
    list of transaction ids; `workflows` a list of (iri, label, target). Either
    may be an EventsUnavailable instance to model a down backend."""

    def __init__(self, txns, workflows):
        super().__init__(server="http://test")
        self._txns = txns
        self._wfs = workflows

    def transactions_since(self, since, limit=1000):
        if isinstance(self._txns, EventsUnavailable):
            raise self._txns
        return [Event(id=i) for i in self._txns if i > since]

    def assigned_workflows(self):
        if isinstance(self._wfs, EventsUnavailable):
            raise self._wfs
        return [qe.Workflow(iri=w[0], label=w[1], target=w[2]) for w in self._wfs]


def test_new_transactions_route_assigned_workflows_and_advance_the_watermark():
    ev = _Fake(txns=[1, 2, 3], workflows=[("uidemo", "UI demo", "CodeModule")])
    state = qe.SubscriptionState()
    routed = []
    r = qe.poll_and_route(ev, state, routed.append)
    assert r.verdict == "live"
    assert r.new_events == 3 and r.routed == 1
    assert state.watermark == 3
    assert [w.iri for w in routed] == ["uidemo"]


def test_no_new_transactions_is_idle_and_holds_the_watermark():
    ev = _Fake(txns=[1, 2], workflows=[("uidemo", "", "")])
    state = qe.SubscriptionState(watermark=2)
    routed = []
    r = qe.poll_and_route(ev, state, routed.append)
    assert r.verdict == "idle"
    assert routed == [] and state.watermark == 2


def test_unreachable_is_cannot_tell_and_keeps_the_watermark():
    ev = _Fake(txns=EventsUnavailable("quipu down"), workflows=[])
    state = qe.SubscriptionState(watermark=5)
    r = qe.poll_and_route(ev, state, lambda _w: None)
    assert r.verdict == "cannot tell"
    assert state.watermark == 5, "a down source must never advance the watermark"


def test_query_failure_after_transactions_does_not_advance():
    # Transactions moved but the workflow query failed — retry, do not drop.
    ev = _Fake(txns=[9], workflows=EventsUnavailable("query 500"))
    state = qe.SubscriptionState(watermark=0)
    r = qe.poll_and_route(ev, state, lambda _w: None)
    assert r.reachable is False
    assert state.watermark == 0


def test_workflows_are_routed_once_across_polls():
    ev = _Fake(txns=[1], workflows=[("uidemo", "", "")])
    state = qe.SubscriptionState()
    first = []
    qe.poll_and_route(ev, state, first.append)
    assert [w.iri for w in first] == ["uidemo"]
    # A later transaction arrives; the SAME workflow is still assigned but must
    # NOT be routed again.
    ev2 = _Fake(txns=[1, 2], workflows=[("uidemo", "", "")])
    second = []
    qe.poll_and_route(ev2, state, second.append)
    assert second == [], "an already-handled workflow must not re-route"
    assert state.watermark == 2


def test_state_round_trips_through_disk(tmp_path):
    state = qe.SubscriptionState(watermark=7, handled={"a", "b"})
    p = tmp_path / "events" / "sub.json"
    state.save(p)
    back = qe.SubscriptionState.load(p)
    assert back.watermark == 7 and back.handled == {"a", "b"}


def test_none_source_runs_the_loop_with_no_backend():
    r = qe.poll_and_route(qe.NoEvents(), qe.SubscriptionState(), lambda _w: None)
    assert r.verdict == "idle"          # the leak detector: works with no Quipu
