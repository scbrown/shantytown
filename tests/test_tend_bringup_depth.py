"""Bring-up order must follow the REPORTING TREE, not the role name (aegis-6snzw).

tend's own header says why the order exists: "boot a lead before its reports, or
their stop events rise with lead-unreachable". That is a statement about the tree.
The sort keyed on `_TIER_ORDER[role]` — a PROXY that does not track it. dearing
measured the gap on this fleet (aegis-j5uek): 7 of 13 live agents are workers
reporting straight to the root, so they sit at depth 1 alongside the leads.

The invariant held anyway, but BY COINCIDENCE — no two agents shared a role while
one reported to the other. It needs only a LEAD REPORTING TO A LEAD: both land in
the same tier, get ordered alphabetically, and the subordinate can boot first.

Both directions here: the shape that used to invert, and the ordinary fleet that
must keep working.
"""
from types import SimpleNamespace

from shantytown.tend import _tree_depth


def _agent(name, role, reports_to=None):
    return SimpleNamespace(name=name, role=role, reports_to=reports_to)


def _order(fleet):
    return [a.name for a in sorted(fleet, key=lambda a: (_tree_depth(a, fleet), a.name))]


def test_a_lead_reporting_to_a_lead_no_longer_boots_before_its_supervisor():
    """THE failure condition. Alphabetically 'billy' < 'dearing', and both are
    leads, so the old (role, name) key put the subordinate first."""
    root = _agent("sattler", "administrator")
    lead = _agent("dearing", "lead", "sattler")
    sub = _agent("billy", "lead", "dearing")        # same role, reports to lead
    fleet = [root, lead, sub]

    assert _tree_depth(sub, fleet) > _tree_depth(lead, fleet), \
        "a subordinate must be deeper than its supervisor, whatever their roles"
    order = _order(fleet)
    assert order.index("dearing") < order.index("billy"), \
        f"subordinate booted before its supervisor: {order}"


def test_an_ordinary_fleet_still_comes_up_root_first():
    """The counterpart: the change must not reorder the shape we actually run.
    A worker reporting straight to the root is depth 1 — the same as a lead — and
    that is CORRECT: neither depends on the other being up."""
    root = _agent("sattler", "administrator")
    lead = _agent("dearing", "lead", "sattler")
    worker = _agent("kelly", "worker", "sattler")
    fleet = [root, lead, worker]

    assert _tree_depth(root, fleet) == 0
    assert _tree_depth(lead, fleet) == 1
    assert _tree_depth(worker, fleet) == 1
    assert _order(fleet)[0] == "sattler", "the root must still come up first"


def test_a_reports_to_CYCLE_sorts_last_and_does_not_hang():
    """tend runs unattended on a timer. A malformed cycle is a config error, and
    bring-up must neither hang nor crash on it — and must not let an agent whose
    tree position cannot be established go FIRST."""
    a = _agent("a", "lead", "b")
    b = _agent("b", "lead", "a")
    root = _agent("sattler", "administrator")
    fleet = [a, b, root]

    assert _tree_depth(a, fleet) > _tree_depth(root, fleet)
    assert _order(fleet)[0] == "sattler"


def test_an_off_roster_parent_is_treated_as_the_root():
    """Same answer the old key gave an unknown ROLE: known-and-shallow, so a
    partial roster cannot reorder everyone else."""
    ghost = _agent("ghost", "worker", "someone-not-here")
    root = _agent("sattler", "administrator")
    assert _tree_depth(ghost, [ghost, root]) == 0
