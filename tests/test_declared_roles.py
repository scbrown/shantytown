"""A DECLARED role outranks the shape (aegis-nyce0l, Stiwi P1).

`derive_agents` computed the single `Agent.role` from the shape of `reports_to`
alone, so a role could only ever be one of three — administrator, lead, worker —
while the live graph declares ELEVEN `aegis:CrewRole`s. Eight of them, `keeper`
among them, were unreachable by any agent no matter what the graph asserted.

The irreducible case is a SOLO ADMINISTRATOR. It has no lead and no reports, which
is the same shape as an ORPHAN, and the old projection returned `worker` for both
on purpose so `roles.check` could flag BROKEN. No inference separates them: the
fact is simply not in the shape. hammond derived as `worker` for that reason.
"""
from shantytown.quipu import derive_agents


def _roles(agents):
    return {a.name: a.role for a in agents}


# --- the shape path must be BYTE-FOR-BYTE what it was ----------------------

def test_shape_is_unchanged_when_nothing_is_declared():
    """The whole safety argument: an un-migrated graph projects as it always did."""
    rows = [{"s": "sattler"}, {"s": "wu", "rt": "sattler"},
            {"s": "malcolm", "rt": "wu"}]
    assert _roles(derive_agents(rows)) == {
        "sattler": "administrator", "wu": "lead", "malcolm": "worker"}
    # and passing an EMPTY declaration is the same answer, not a different path
    assert _roles(derive_agents(rows, {})) == _roles(derive_agents(rows))


def test_an_orphan_still_derives_worker_so_roles_check_still_flags_it():
    """Undeclared + unattached must stay BROKEN-able. This is the regression that
    a careless 'declared wins' would silently delete."""
    assert _roles(derive_agents([{"s": "nobody"}])) == {"nobody": "worker"}


# --- ARM 2: the solo administrator, which shape CANNOT express -------------

def test_a_solo_admin_that_declares_administrator_renders_administrator():
    rows = [{"s": "hammond"}]
    assert _roles(derive_agents(rows))["hammond"] == "worker", (
        "control: without a declaration the shape still says worker")
    agents = derive_agents(rows, {"hammond": ("administrator",)})
    assert _roles(agents)["hammond"] == "administrator"
    assert agents[0].reports_to is None, "a solo admin reports to nobody"


def test_a_declaration_overrides_a_shape_that_disagrees():
    """Not just filling a gap — it OVERRIDES. A member with a lead derives
    `worker`; declaring `keeper` makes it a keeper."""
    rows = [{"s": "dearing"}, {"s": "arnold", "rt": "dearing"}]
    assert _roles(derive_agents(rows))["arnold"] == "worker"
    assert _roles(derive_agents(rows, {"arnold": ("keeper",)}))["arnold"] == "keeper"


# --- the stack is carried, and deliberately NOT reduced to one name --------

def test_a_stack_is_carried_but_does_not_elect_a_primary_role():
    """aegis-cqgq1 landed precedence as PER-AXIS-VALUE ranking and explicitly
    rejected per-role ranking ('option (a), which misranks multi-axis roles').
    So a stacked member keeps its shape-derived TREE POSITION in `role`, and its
    set stays in `roles` for traits.Catalog to resolve per axis.

    arnold is the live case: it declares worker + keeper + escalation-target.
    """
    rows = [{"s": "dearing"}, {"s": "arnold", "rt": "dearing"}]
    stack = ("escalation-target", "keeper", "worker")
    agents = {a.name: a for a in derive_agents(rows, {"arnold": stack})}
    assert agents["arnold"].role == "worker", (
        "a 3-role stack must NOT silently elect one name here")
    assert agents["arnold"].roles == stack, "but the stack must be carried"
    assert agents["arnold"].effective_roles() == stack, (
        "and effective_roles() is what a consumer acts on — keeper IS reachable")


def test_the_carried_stack_is_what_effective_roles_returns():
    """An undeclared member falls back to its tree position, a declared one does
    not. This is the line that makes `keeper` reachable at all."""
    rows = [{"s": "dearing"}, {"s": "muldoon", "rt": "dearing"}]
    undeclared = derive_agents(rows)[1]
    assert undeclared.effective_roles() == ("worker",)
    declared = derive_agents(rows, {"muldoon": ("keeper",)})[1]
    assert declared.effective_roles() == ("keeper",)


# --- ARM 1: what `st crew` RENDERS -----------------------------------------
#
# sattler's ruling 2026-09-16: render the stack, do not elect from it. The cell
# is the declared roles joined by ","; an undeclared member falls back to its
# tree position, so an un-migrated fleet renders exactly as it always did.

def _cell(agent):
    """The crew-table role cell, as cli.py builds it."""
    return ",".join(agent.effective_roles())


def test_a_declared_keeper_renders_the_stack_not_the_tree_position():
    rows = [{"s": "dearing"}, {"s": "arnold", "rt": "dearing"}]
    before = derive_agents(rows)[0]
    assert _cell(before) == "worker", (
        "control: undeclared, arnold renders its tree position")
    after = derive_agents(rows, {"arnold": ("keeper", "worker")})[0]
    assert _cell(after) == "keeper,worker"


def test_an_undeclared_member_renders_exactly_as_before():
    rows = [{"s": "sattler"}, {"s": "wu", "rt": "sattler"},
            {"s": "muldoon", "rt": "wu"}]
    # derive_agents sorts by name: muldoon, sattler, wu
    assert {a.name: _cell(a) for a in derive_agents(rows)} == {
        "muldoon": "worker", "sattler": "administrator", "wu": "lead"}


def test_an_orphan_renders_worker_and_stays_flaggable():
    """BROKEN must survive the change: an orphan declares nothing, so it renders
    its tree position and `roles.check` can still fault it."""
    orphan = derive_agents([{"s": "nobody"}])[0]
    assert _cell(orphan) == "worker"
    assert orphan.roles == (), "nothing declared, so nothing to render"
    assert orphan.reports_to is None
