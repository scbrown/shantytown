from __future__ import annotations

import json

from shantytown import dream


def _candidate(agent="arnold", harness="codex", headroom=80,
               ordinary_dispatchable=False):
    return {"agent": agent, "harness": harness, "headroom": headroom,
            "ordinary_dispatchable": ordinary_dispatchable}


def test_default_is_off_and_signal_absence_is_not_spare_capacity():
    plan, why = dream.plan(dream.Policy(), {}, [], [], now=100)
    assert plan is None and why == "dreaming is disabled"
    plan, why = dream.plan(dream.Policy(enabled=True), {}, [], [], now=100)
    assert plan is None and "measured spare capacity" in why


def test_dispatchable_normal_work_preempts_dreaming_for_that_provider():
    plan, why = dream.plan(dream.Policy(enabled=True), {},
                           [{"id": "aegis-real", "labels": ["bug"]}],
                           [_candidate(ordinary_dispatchable=True)], now=100)
    assert plan is None and why == "normal work is dispatchable to every idle provider"


def test_ready_but_undispatchable_work_does_not_suppress_dreaming():
    cycle, why = dream.plan(
        dream.Policy(enabled=True), {},
        [{"id": "aegis-held", "labels": ["bug"]}],
        [_candidate(ordinary_dispatchable=False)], now=100)
    assert cycle is not None and why == ""


def test_one_existing_dream_bounds_the_queue():
    plan, why = dream.plan(dream.Policy(enabled=True), {},
                           [{"id": "aegis-d", "labels": ["dream-proposal"]}],
                           [_candidate()], now=100)
    assert plan is None and why == "a dream cycle is already queued"


def test_interval_and_headroom_are_hard_gates():
    policy = dream.Policy(enabled=True, interval_minutes=60, min_headroom_pct=25)
    assert dream.plan(policy, {"last_at": 50}, [], [_candidate(headroom=90)],
                      now=100)[0] is None
    assert dream.plan(policy, {}, [], [_candidate(headroom=24)], now=4000)[0] is None


def test_missing_dispatchability_evidence_fails_closed():
    candidate = {"agent": "arnold", "harness": "codex", "headroom": 80}
    plan, why = dream.plan(dream.Policy(enabled=True), {}, [], [candidate], now=100)
    assert plan is None and "dispatchable" in why


def test_rotation_alternates_mode_and_domain_and_picks_most_headroom():
    policy = dream.Policy(enabled=True, domains=("ontology", "infra"))
    cycle, why = dream.plan(
        policy, {"last_mode": "consolidate", "last_domain": "ontology"}, [],
        [_candidate("claude", "claude", 30), _candidate("codex", "codex", 90)],
        now=100)
    assert why == ""
    assert (cycle.agent, cycle.mode, cycle.domain) == ("codex", "dream", "infra")
    assert cycle.labels == "dream,dream-proposal"
    assert "Do not implement" in cycle.description


def test_consolidation_is_read_mostly_and_emits_discrepancies():
    cycle, _ = dream.plan(dream.Policy(enabled=True), {}, [], [_candidate()], now=100)
    assert cycle.mode == "consolidate"
    assert cycle.labels == "dream,dream-discrepancy"
    assert "do not mutate infrastructure, code" in cycle.description


def test_state_advances_only_when_caller_records_observed_create(tmp_path):
    state = dream.State(tmp_path)
    cycle, _ = dream.plan(dream.Policy(enabled=True), {}, [], [_candidate()], now=100)
    assert state.read() == {}
    state.record(cycle, "aegis-made", now=123)
    assert json.loads(state.path.read_text())["last_item"] == "aegis-made"
    assert state.read()["last_at"] == 123
