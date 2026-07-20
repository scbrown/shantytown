"""The orchestration tier.

Every decision branch has a test that OBSERVES it firing. This suite exists in
the shadow of the dead-CLEAR-branch bug, where a triage branch that could never fire passed its
tests because the tests were built to fit the proxy, not to exercise the system.
So: each branch is reached from realistic state, and the refusals are shown
refusing for the RIGHT reason (the message names the rule), not merely raising.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown.files import FilesRegistry
from shantytown.protocols import Agent
from shantytown.tier import (
    Capacity, Decision, LeadState, Reason, Routing,
    handle_stop, plan_role_set, release, role_set, route_stop,
)


def reg(tmp_path: Path, **agents) -> FilesRegistry:
    d = tmp_path / "crew"; d.mkdir()
    for name, spec in agents.items():
        (d / f"{name}.json").write_text(json.dumps(spec))
    return FilesRegistry(d)


# --- role set: generative, and refuses at plan time -----------------------

def test_role_set_lead_writes_cards_and_routes(tmp_path):
    r = reg(tmp_path,
            malcolm={"role": "worker"},
            ellie={"role": "worker"},
            ian={"role": "worker"})
    plan = role_set(r, "malcolm", "lead", reports=["ellie", "ian"])
    assert r.get("malcolm").role == "lead"
    assert r.get("ellie").reports_to == "malcolm"
    assert r.get("ian").reports_to == "malcolm"
    # generative: the routing was emitted, not just the card
    assert ("ellie", "malcolm") in plan.routes
    assert ("ian", "malcolm") in plan.routes


def test_role_set_dry_run_writes_nothing(tmp_path):
    r = reg(tmp_path, malcolm={"role": "worker"}, ellie={"role": "worker"})
    role_set(r, "malcolm", "lead", reports=["ellie"], dry_run=True)
    assert r.get("malcolm").role == "worker", "dry-run mutated the registry"
    assert r.get("ellie").reports_to is None


def test_Q1_lead_cannot_report_to_a_lead(tmp_path):
    """RULED depth 2. The refusal must name the rule, not just raise."""
    r = reg(tmp_path,
            arnold={"role": "administrator"},
            malcolm={"role": "lead", "reports_to": "arnold"},
            wu={"role": "worker", "reports_to": "malcolm"})
    with pytest.raises(ValueError, match="depth 2|lead under a lead|cannot report to another lead"):
        role_set(r, "wu", "lead", reports=[])


def test_Q1_report_that_is_a_lead_is_refused(tmp_path):
    r = reg(tmp_path,
            malcolm={"role": "worker"},
            sub={"role": "lead"})
    with pytest.raises(ValueError, match="cannot report to another lead|depth 2"):
        role_set(r, "malcolm", "lead", reports=["sub"])


def test_demote_to_worker_refuses_to_strand_reports(tmp_path):
    r = reg(tmp_path,
            malcolm={"role": "lead"},
            ellie={"role": "worker", "reports_to": "malcolm"})
    with pytest.raises(ValueError, match="strand"):
        role_set(r, "malcolm", "worker")


def test_demote_to_worker_succeeds_when_no_reports(tmp_path):
    r = reg(tmp_path, malcolm={"role": "lead", "reports_to": "arnold"})
    role_set(r, "malcolm", "worker")
    assert r.get("malcolm").role == "worker"


def test_unknown_role_refused(tmp_path):
    r = reg(tmp_path, x={"role": "worker"})
    with pytest.raises(ValueError, match="unknown role"):
        plan_role_set(r, "x", "overlord")


def test_administrator_reports_to_nobody(tmp_path):
    r = reg(tmp_path, arnold={"role": "worker"})
    role_set(r, "arnold", "administrator")
    assert r.get("arnold").reports_to is None


# --- stop-hook routing: Q3 and Q4 ------------------------------------------

def _hier(tmp_path):
    return reg(tmp_path,
               arnold={"role": "administrator"},
               malcolm={"role": "lead", "reports_to": "arnold"},
               ellie={"role": "worker", "reports_to": "malcolm"},
               loner={"role": "worker"})  # no lead


def test_worker_stop_reaches_its_lead(tmp_path):
    rt = route_stop(_hier(tmp_path), "ellie")
    assert rt.to == "malcolm"
    assert rt.rose is False


def test_Q3_lead_down_rises_to_admin_LOUDLY(tmp_path):
    """The one most likely to be got wrong. Must rise AND name the reason."""
    r = _hier(tmp_path)
    rt = route_stop(r, "ellie", lead_is_up=lambda n: n != "malcolm")
    assert rt.to == "arnold"
    assert rt.rose is True
    assert rt.reason is Reason.LEAD_UNREACHABLE, "rose silently — Q3 requires the reason"


def test_Q3_positive_control_lead_up_does_NOT_rise(tmp_path):
    """The control proving the rise above is real: with the lead UP, no rise."""
    r = _hier(tmp_path)
    rt = route_stop(r, "ellie", lead_is_up=lambda n: True)
    assert rt.rose is False, "rise fired even with the lead up — the test can't discriminate"


def test_Q4_worker_with_no_lead_goes_to_admin_directly(tmp_path):
    rt = route_stop(_hier(tmp_path), "loner")
    assert rt.to == "arnold"
    assert rt.rose is False


def test_no_lead_and_no_admin_is_an_error_not_a_silent_drop(tmp_path):
    r = reg(tmp_path, loner={"role": "worker"})
    with pytest.raises(LookupError, match="goes nowhere|no administrator"):
        route_stop(r, "loner")


# --- absorb / delegate / escalate, and the rule that keeps a lead a lead ---

def test_absorb_light_work(tmp_path):
    s = LeadState("malcolm")
    h = handle_stop(s, "item-1", is_light=True)
    assert h.decision is Decision.ABSORB
    assert s.absorbed == "item-1"


def test_second_absorb_is_REFUSED_not_queued(tmp_path):
    """The rule that keeps a lead a lead. A second absorbed task = collapse."""
    s = LeadState("malcolm")
    handle_stop(s, "item-1", is_light=True)
    with pytest.raises(Capacity, match="already absorbing|tier collapsed"):
        handle_stop(s, "item-2", is_light=True)


def test_release_lets_it_absorb_again(tmp_path):
    s = LeadState("malcolm")
    handle_stop(s, "item-1", is_light=True)
    release(s, "item-1")
    h = handle_stop(s, "item-2", is_light=True)
    assert h.decision is Decision.ABSORB


def test_delegate(tmp_path):
    s = LeadState("malcolm")
    h = handle_stop(s, "item-1", is_light=False, delegate_to="ian")
    assert h.decision is Decision.DELEGATE
    assert "ian" in h.note


def test_escalate_carries_a_reason(tmp_path):
    s = LeadState("malcolm")
    h = handle_stop(s, "item-1", is_light=False, escalate_reason=Reason.NEEDS_DECISION)
    assert h.decision is Decision.ESCALATE
    assert h.reason is Reason.NEEDS_DECISION


def test_busy_is_NOT_an_escalation_reason(tmp_path):
    """'I was busy' must surface as capacity, not launder as an escalation.

    There is no Reason.BUSY. A lead that can't absorb a second task raises
    Capacity (test above) — it does not get to escalate 'I'm full' as if the WORK
    needed the administrator.
    """
    assert not any(r.value == "busy" for r in Reason)
    assert not hasattr(Reason, "BUSY")


def test_not_light_with_no_decision_refuses(tmp_path):
    """A lead must DECIDE — silence is not a fourth option."""
    s = LeadState("malcolm")
    with pytest.raises(ValueError, match="must DECIDE|silence is not"):
        handle_stop(s, "item-1", is_light=False)


def test_absorb_rate_is_a_query_not_a_vibe(tmp_path):
    s = LeadState("malcolm")
    handle_stop(s, "a", is_light=True); release(s, "a")
    handle_stop(s, "b", is_light=True); release(s, "b")
    handle_stop(s, "c", is_light=False, delegate_to="x")
    assert abs(s.absorb_rate - 2 / 3) < 1e-9
    # a lead that never delegates is now detectable
    s2 = LeadState("greedy")
    for i in range(5):
        handle_stop(s2, f"i{i}", is_light=True); release(s2, f"i{i}")
    assert s2.absorb_rate == 1.0, "100% absorb — the tier isn't working, and it's queryable"
