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


def test_periodic_quota_queues_behind_dispatchable_normal_work():
    cycle, why = dream.plan(dream.Policy(enabled=True), {},
                            [{"id": "aegis-real", "labels": ["bug"]}],
                            [_candidate(ordinary_dispatchable=True)], now=100)
    assert cycle is not None and why == ""


def test_ready_but_undispatchable_work_does_not_suppress_dreaming():
    cycle, why = dream.plan(
        dream.Policy(enabled=True), {},
        [{"id": "aegis-held", "labels": ["bug"]}],
        [_candidate(ordinary_dispatchable=False)], now=100)
    assert cycle is not None and why == ""


def test_one_existing_cycle_bounds_the_queue():
    plan, why = dream.plan(dream.Policy(enabled=True), {},
                           [{"id": "aegis-d", "labels": ["dream", "dream-cycle"]}],
                           [_candidate()], now=100)
    assert plan is None and why == "a dream cycle is already queued"


def test_unassigned_dream_outputs_do_not_hold_the_queue():
    # aegis-2idcev: an unassigned dream-proposal (kpbn61) held the lane 11 days,
    # and so did a bead merely labelled dream.
    cycle, why = dream.plan(
        dream.Policy(enabled=True), {},
        [{"id": "aegis-kpbn61", "labels": ["dream", "dream-proposal"], "title": "proposal"},
         {"id": "aegis-2idcev", "labels": ["dream", "shantytown"], "title": "DREAM lane stalled"},
         {"id": "aegis-disc", "labels": ["dream-discrepancy"]}],
        [_candidate()], now=100)
    assert cycle is not None and why == ""


def test_a_legacy_cycle_is_recognised_by_its_title():
    plan, why = dream.plan(
        dream.Policy(enabled=True), {},
        [{"id": "aegis-old", "labels": ["dream", "dream-proposal"],
          "title": "DREAM propose: improve infra"}],
        [_candidate()], now=100)
    assert plan is None and why == "a dream cycle is already queued"


def test_a_lapsed_cycle_stops_holding_the_queue():
    item = {"id": "aegis-c", "labels": ["dream", "dream-cycle"],
            "updated_at": "2026-10-07T00:00:00Z"}
    t0 = dream._epoch(item["updated_at"])
    held = dream.plan(dream.Policy(enabled=True), {}, [item], [_candidate()],
                      now=t0 + dream.CYCLE_IDLE_LIMIT_S - 1)
    assert held[0] is None and held[1] == "a dream cycle is already queued"
    cycle, why = dream.plan(dream.Policy(enabled=True), {}, [item], [_candidate()],
                            now=t0 + dream.CYCLE_IDLE_LIMIT_S + 1)
    assert cycle is not None and why == ""


def test_an_unreadable_timestamp_keeps_the_bound():
    item = {"id": "aegis-c", "labels": ["dream-cycle"], "updated_at": "not a time"}
    assert dream.plan(dream.Policy(enabled=True), {}, [item], [_candidate()],
                      now=10**10)[0] is None


def test_new_cycles_carry_the_cycle_label():
    for state in ({}, {"last_mode": "consolidate"}):
        cycle, _ = dream.plan(dream.Policy(enabled=True), state, [], [_candidate()], now=100)
        assert "dream-cycle" in cycle.labels.split(",")


def test_cycles_spread_away_from_the_last_agent():
    cands = [_candidate("malcolm", "claude", 90), _candidate("arnold", "codex", 60)]
    cycle, _ = dream.plan(dream.Policy(enabled=True), {"last_agent": "malcolm"}, [], cands, now=100)
    assert cycle.agent == "arnold"
    alone, _ = dream.plan(dream.Policy(enabled=True), {"last_agent": "malcolm"}, [],
                          cands[:1], now=100)
    assert alone.agent == "malcolm", "the last agent still goes when nobody else is eligible"


def test_assigned_dream_output_does_not_poison_the_queue_gate():
    cycle, why = dream.plan(
        dream.Policy(enabled=True), {},
        [{"id": "aegis-output", "labels": ["dream-proposal"],
          "assignee": "ian"}],
        [_candidate()], now=100)
    assert cycle is not None and why == ""


def test_mixed_state_blocks_only_for_the_unassigned_cycle():
    plan, why = dream.plan(
        dream.Policy(enabled=True), {},
        [{"id": "aegis-output", "labels": ["dream-proposal"],
          "assignee": "ian"},
         {"id": "aegis-queued", "labels": ["dream", "dream-cycle"], "assignee": ""}],
        [_candidate()], now=100)
    assert plan is None and why == "a dream cycle is already queued"


def test_interval_and_headroom_are_hard_gates():
    policy = dream.Policy(enabled=True, interval_minutes=60, min_headroom_pct=25)
    assert dream.plan(policy, {"last_at": 50}, [], [_candidate(headroom=90)],
                      now=100)[0] is None
    assert dream.plan(policy, {}, [], [_candidate(headroom=24)], now=4000)[0] is None


def test_dispatchability_evidence_is_not_required_for_periodic_quota():
    candidate = {"agent": "arnold", "harness": "codex", "headroom": 80}
    cycle, why = dream.plan(dream.Policy(enabled=True), {}, [], [candidate], now=100)
    assert cycle is not None and why == ""


def test_rotation_alternates_mode_and_domain_and_picks_most_headroom():
    policy = dream.Policy(enabled=True, domains=("ontology", "infra"))
    cycle, why = dream.plan(
        policy, {"last_mode": "consolidate", "last_domain": "ontology"}, [],
        [_candidate("claude", "claude", 30), _candidate("codex", "codex", 90)],
        now=100)
    assert why == ""
    assert (cycle.agent, cycle.mode, cycle.domain) == ("codex", "dream", "infra")
    assert cycle.labels == "dream,dream-proposal,dream-cycle"
    assert "Do not implement" in cycle.description


def test_consolidation_is_read_mostly_and_emits_discrepancies():
    cycle, _ = dream.plan(dream.Policy(enabled=True), {}, [], [_candidate()], now=100)
    assert cycle.mode == "consolidate"
    assert cycle.labels == "dream,dream-discrepancy,dream-cycle"
    assert "do not mutate infrastructure, code" in cycle.description


def test_state_advances_only_when_caller_records_observed_create(tmp_path):
    state = dream.State(tmp_path)
    cycle, _ = dream.plan(dream.Policy(enabled=True), {}, [], [_candidate()], now=100)
    assert state.read() == {}
    state.record(cycle, "aegis-made", now=123)
    assert json.loads(state.path.read_text())["last_item"] == "aegis-made"
    assert state.read()["last_at"] == 123
