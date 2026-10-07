"""Stop-recipient eligibility is a TRAIT, not a literal (aegis-vj3uet PR A).

route_stop and the runtime capability gate used to hold their own copy of
"lead or administrator". These tests pin the one predicate both now ask
(traits.receives_stops): built-in answers are unchanged, a deployment-declared
router works with no code edit, and an unresolvable role stack keeps its old
answer instead of being guessed into (or out of) the recipient set.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shantytown import runtime
from shantytown.files import FilesRegistry
from shantytown.protocols import Agent
from shantytown.tier import route_stop
from shantytown.traits import Catalog, receives_stops

KEEPER = {"keeper": {"attachment": ["reports-to"],
                     "coordination": ["absorbs", "dispatches"],
                     "workIntake": ["dispatched", "self-directed"]}}


def reg(tmp_path: Path, **agents) -> FilesRegistry:
    d = tmp_path / "crew"; d.mkdir()
    for name, spec in agents.items():
        (d / f"{name}.json").write_text(json.dumps(spec))
    return FilesRegistry(d)


@pytest.mark.parametrize("role,expected", [
    ("administrator", True), ("lead", True), ("worker", False)])
def test_builtin_answers_match_the_old_literal(role, expected):
    assert receives_stops(Agent(name="x", role=role)) is expected
    assert (role in runtime._ROLES_NEEDING_STOP) is expected


def test_declared_keeper_is_a_router_with_no_code_edit(tmp_path):
    r = reg(tmp_path,
            sattler={"role": "administrator"},
            dearing={"role": "keeper", "reports_to": "sattler"},
            ian={"role": "worker", "reports_to": "dearing"})
    routed = route_stop(r, "ian", catalog=Catalog(KEEPER))
    assert routed.to == "dearing" and not routed.rose


def test_keeper_down_rises_to_the_administrator_loudly(tmp_path):
    r = reg(tmp_path,
            sattler={"role": "administrator"},
            dearing={"role": "keeper", "reports_to": "sattler"},
            ian={"role": "worker", "reports_to": "dearing"})
    routed = route_stop(r, "ian", lead_is_up=lambda _n: False, catalog=Catalog(KEEPER))
    assert routed.to == "sattler" and routed.rose


def test_without_the_declaration_a_keeper_is_still_refused(tmp_path):
    # The built-in catalog does not describe keeper: the old literal decides,
    # and it says no. Nothing became a router by accident.
    r = reg(tmp_path,
            sattler={"role": "administrator"},
            dearing={"role": "keeper", "reports_to": "sattler"},
            ian={"role": "worker", "reports_to": "dearing"})
    with pytest.raises(LookupError, match="not a stop recipient"):
        route_stop(r, "ian")


def test_unresolvable_stack_keeps_its_old_answer():
    # The live fleet stacks graph-only roles. Against a catalog that does not
    # know them, a WORKER must not become a recipient and a LEAD must not stop
    # being one.
    arnold = Agent(name="arnold", role="worker", roles=("escalation-target", "keeper", "worker"))
    wu = Agent(name="wu", role="lead", roles=("lead", "made-up"))
    assert receives_stops(arnold) is False
    assert receives_stops(wu) is True


def test_router_and_capability_gate_cannot_disagree():
    cat = Catalog(KEEPER)
    for card in (Agent(name="a", role="administrator"), Agent(name="l", role="lead"),
                 Agent(name="w", role="worker"), Agent(name="k", role="keeper"),
                 Agent(name="s", role="worker", roles=("worker", "keeper"))):
        assert runtime.needs_stop_delivery(card, cat) == receives_stops(card, cat)
