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
from shantytown.tier import (LeadStatus,
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


class _NonBlockingHarness:
    """A harness that cannot deliver stop events — the codex-class program the
    capability gate exists to refuse for a lead/administrator (aegis-w5l9)."""
    name = "codex"

    def hooks(self, card):
        from shantytown.runtime import HookSpec
        return HookSpec(blocking_stop=False)


def test_role_set_refuses_a_lead_whose_harness_cannot_host_it(tmp_path, monkeypatch):
    """aegis-w5l9: the capability gate fires at role-set time, BEFORE any write.
    A lead whose harness lacks blocking stop hooks stays worker and NOTHING is
    written — the refusal adapters.md documented, which previously only fired at
    `st new` launch (after the card was already on disk)."""
    from shantytown.runtime import CapabilityError
    monkeypatch.setattr("shantytown.harness.for_card", lambda card: _NonBlockingHarness())
    r = reg(tmp_path, malcolm={"role": "worker"})

    with pytest.raises(CapabilityError, match="blocking stop hooks") as ei:
        role_set(r, "malcolm", "lead")

    # The refusal genuinely left nothing written — the card is still a worker.
    assert r.get("malcolm").role == "worker", "gate did not fire before the write"
    # And the message tells the truth for THIS site: nothing written, and it does
    # NOT claim "nothing launched" (that is the launch site's true consequence).
    assert "Nothing written." in str(ei.value)
    assert "launched" not in str(ei.value).lower()


def test_role_set_dry_run_surfaces_the_capability_refusal(tmp_path, monkeypatch):
    """The gate is a property of the PLAN, not the write, so --dry-run shows it
    too — uniform with the hierarchy refusals."""
    from shantytown.runtime import CapabilityError
    monkeypatch.setattr("shantytown.harness.for_card", lambda card: _NonBlockingHarness())
    r = reg(tmp_path, malcolm={"role": "worker"})
    with pytest.raises(CapabilityError, match="blocking stop hooks"):
        role_set(r, "malcolm", "lead", dry_run=True)


def test_role_set_worker_is_not_gated(tmp_path, monkeypatch):
    """The gate keys on the NEW role needing stop delivery: demoting/keeping a
    worker on a non-blocking harness is fine — only lead/administrator receive."""
    from shantytown.protocols import Agent
    monkeypatch.setattr("shantytown.harness.for_card", lambda card: _NonBlockingHarness())
    r = reg(tmp_path, malcolm={"role": "lead", "reports_to": "arnold"},
            arnold={"role": "administrator"})
    # malcolm -> worker: a worker needs no stop capability, so the non-blocking
    # harness must not block the demotion.
    role_set(r, "malcolm", "worker")
    assert r.get("malcolm").role == "worker"


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


# --- lead-unreachable must distinguish its TWO causes (internal-ref) ----------
#
# "the lead is DOWN" and "the lead is UP but cannot drain" want OPPOSITE actions
# — restart vs relaunch — and were one string. The second fired ~6 times in one
# evening during a restructure and was absorbed as noise every time, because the
# operator could SEE the lead was up while the alert said unreachable.

def test_lead_unreachable_carries_WHY_when_the_probe_knows(tmp_path):
    r = _hier(tmp_path)
    status = LeadStatus(False, "malcolm is UP but CANNOT DRAIN: relaunch it")
    rt = route_stop(r, "ellie", lead_is_up=lambda n: status)
    assert rt.rose is True and rt.reason is Reason.LEAD_UNREACHABLE
    assert "CANNOT DRAIN" in rt.detail, "rose without saying which kind of unreachable"
    assert "CANNOT DRAIN" in rt.render(), "the detail never reaches the rendered line"


def test_the_two_causes_are_DISTINGUISHABLE_not_just_decorated(tmp_path):
    """The discrimination test: two different falsy verdicts must not render the
    same. Without this, `detail` could be a constant and every assertion above
    would still pass."""
    r = _hier(tmp_path)
    down = route_stop(r, "ellie",
                      lead_is_up=lambda n: LeadStatus(False, "malcolm is DOWN — restart it"))
    deaf = route_stop(r, "ellie",
                      lead_is_up=lambda n: LeadStatus(False, "malcolm is UP but CANNOT DRAIN"))
    assert down.detail != deaf.detail
    assert "restart" in down.detail and "CANNOT DRAIN" in deaf.detail


def test_a_plain_BOOL_predicate_still_works_unchanged(tmp_path):
    """Back-compat is load-bearing: route_stop carries stop events, and every
    caller that returns a bare bool must keep routing. Absent detail stays
    EMPTY — never invented, because a fabricated cause on an escalation is
    worse than no cause."""
    r = _hier(tmp_path)
    rt = route_stop(r, "ellie", lead_is_up=lambda n: False)
    assert rt.rose is True and rt.reason is Reason.LEAD_UNREACHABLE
    assert rt.detail == ""
    assert "ROSE: lead-unreachable)" in rt.render()


def test_a_truthy_LeadStatus_does_NOT_rise(tmp_path):
    """Control: LeadStatus(True) must behave exactly like True."""
    r = _hier(tmp_path)
    rt = route_stop(r, "ellie", lead_is_up=lambda n: LeadStatus(True))
    assert rt.rose is False, "a reachable lead rose — __bool__ is not wired"


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


# --- strays: the sessions no card claims (aegis-np4x1) -----------------------

from shantytown.tier import strays


ROSTER = ["dearing", "goldblum", "ian", "malcolm", "maldoon", "sentinel"]
CARDS = [f"shanty-{n}" for n in ROSTER]


def test_a_retired_naming_scheme_is_named_not_merely_counted():
    """THE aegis-np4x1 BLIND SPOT. Six agents ran as aegis-crew-* beside the
    shanty-* roster and `st crew` reported 19 agents, 0 faults — because every
    check it owns is addressed BY NAME, and nothing ever enumerated the socket."""
    live = CARDS + ["aegis-crew-goldblum", "aegis-crew-ian", "aegis-crew-malcolm"]
    got = strays(live, CARDS, ROSTER)
    assert got == [("aegis-crew-goldblum", "goldblum"),
                   ("aegis-crew-ian", "ian"),
                   ("aegis-crew-malcolm", "malcolm")], got


def test_a_clean_socket_reports_nothing():
    """The control. A detector never observed staying quiet is an alarm, not a
    detector — and this one runs on every `st crew`."""
    assert strays(CARDS, CARDS, ROSTER) == []


def test_a_roster_name_INSIDE_another_name_is_not_a_duplicate():
    """`ian` is a substring of `sebastian`, and the remedy for a real duplicate
    is killing a session — so a substring match would point a human at a live
    agent's pane and call it debris. Match the last segment, never a substring."""
    got = strays(["shanty-sebastian", "ianitor", "guardian"], CARDS, ROSTER)
    assert [who for _, who in got] == [None, None, None], got


def test_an_unrecognised_session_is_still_reported_but_not_blamed():
    """Weaker signal, still reported: dropping it would rebuild the same blind
    spot one size smaller. agent=None is the difference between `this is your
    agent, twice` and `st cannot name this`."""
    got = strays(CARDS + ["scratch"], CARDS, ROSTER)
    assert got == [("scratch", None)]


def test_a_card_that_names_an_odd_pane_is_not_its_own_stray():
    """Cards keep whatever pane name they were written with (pane_for never
    overwrites one). A roster whose panes are NOT `shanty-<name>` must not have
    its entire own fleet reported as impostors."""
    odd = ["p-ian", "crew-goldblum"]
    assert strays(odd, odd, ROSTER) == []
