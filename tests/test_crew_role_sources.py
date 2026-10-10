from shantytown.dashboard import crew_role_cell, crew_role_sources
from shantytown.protocols import Agent


def test_mixed_roles_keep_both_sources_and_all_declared_values():
    agent = Agent("example", role="worker", roles=("worker", "normal", "gaming"))
    sources = crew_role_sources(agent, {"normal", "gaming"})
    assert crew_role_cell(sources) == "tree=worker | traits=normal,gaming"
    assert set(sources["tree"] + sources["traits"]) == set(agent.effective_roles())


def test_tree_stack_and_unmigrated_member_have_no_empty_separator():
    agent = Agent("example", role="worker", roles=("escalation-target", "keeper", "worker"))
    assert crew_role_cell(crew_role_sources(agent, {"gaming"})) == (
        "tree=worker,escalation-target,keeper")
    assert crew_role_cell(crew_role_sources(Agent("example", role="lead"), {})) == "tree=lead"


def test_overlapping_catalog_entry_preserves_both_attributions():
    agent = Agent("example", role="keeper", roles=("keeper", "normal"))
    assert crew_role_cell(crew_role_sources(agent, {"keeper", "normal"})) == (
        "tree=keeper | traits=keeper,normal")


def test_legacy_peer_stack_is_attributed_without_inventing_its_catalog():
    assert crew_role_cell({"declared": ["worker", "gaming"]}) == "declared=worker,gaming"
