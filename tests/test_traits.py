"""A role is a TRAIT SET the deployment declares — not an enum st ships (GH #37).

The fourth layer #13 closed without. `VALID_ROLES = (worker, lead, administrator)`
was not merely a vocabulary: it made role == tree-position, so the reports-to rules
applied to every role and an advisor — consulted, reporting to nobody, with nobody
reporting to it — could not exist. Adding one meant editing this package.

Two properties carry the weight here and both have a bug behind them:

  1. UNATTACHED SKIPS THE TREE RULES. That single conditional is the unlock.
  2. AN UNRANKED CONFLICT REFUSES. A stacked {worker, keeper} is scope
     {single-task, domain-scoped} at once. "Most-specific wins" is prose; a
     tie-break written into traits.py would be the closed enum one layer up, which
     is the exact thing the trait model exists to kill.
"""
from __future__ import annotations

import pytest

from shantytown import config, quipu, roles as roles_mod, tier, traits
from shantytown.protocols import Agent


# --- composition -------------------------------------------------------------

def test_the_builtin_three_are_described():
    c = traits.default_catalog()
    assert c.known() == ["administrator", "lead", "worker"], \
        "only the three the built-in PROCESS is defined for — a catalog of ten " \
        "here would re-hardcode what the deployment is supposed to declare"
    assert c.of("administrator").attachment == "rooted"
    assert c.of("lead").absorbs and c.of("lead").dispatches
    assert not c.of("worker").absorbs


def test_multi_axes_UNION_across_a_stack():
    """The reason the enum failed: a worker is dispatched AND self-directed."""
    c = traits.Catalog({"keeper": {"workIntake": ["consulted"],
                                   "coordination": ["neither"]}})
    t = c.of(["worker", "keeper"])
    assert t.workIntake == frozenset({"dispatched", "self-directed", "consulted"})


def test_a_single_axis_conflict_RESOLVES_from_declared_precedence():
    c = traits.Catalog(
        {"keeper": {"scope": "domain-scoped", "authority": "veto-in-scope"}},
        {("scope", "domain-scoped"): 2, ("scope", "single-task"): 1,
         ("authority", "veto-in-scope"): 3, ("authority", "none"): 1})
    t = c.of(["worker", "keeper"])
    assert t.scope == "domain-scoped"          # beats worker's single-task
    assert t.authority == "veto-in-scope"      # beats worker's none


def test_an_UNRANKED_conflict_REFUSES_rather_than_guessing():
    """THE rule not to soften. st picking a winner here is the closed enum, one
    layer up — and it would be invisible, which is worse than the enum was."""
    c = traits.Catalog({"keeper": {"scope": "domain-scoped"}})   # no precedence
    with pytest.raises(traits.AmbiguousTrait) as e:
        c.of(["worker", "keeper"])
    msg = str(e.value)
    assert "scope" in msg and "precedence" in msg
    assert "domain-scoped" in msg and "single-task" in msg, "it names both values"


def test_a_TIE_in_the_declared_ranks_also_refuses():
    c = traits.Catalog({"keeper": {"scope": "domain-scoped"}},
                       {("scope", "domain-scoped"): 2, ("scope", "single-task"): 2})
    with pytest.raises(traits.AmbiguousTrait, match="SAME precedence"):
        c.of(["worker", "keeper"])


def test_an_axis_NOTHING_declares_stays_None():
    """A partial role is real: the live escalation-target declares an intake and no
    attachment. Inventing one would put an agent in the tree on the strength of a
    role that never mentioned the tree."""
    c = traits.Catalog({"escalation-target": {"workIntake": ["escalations-only"]}},
                       builtin=False)
    t = c.of("escalation-target")
    assert t.attachment is None
    assert t.unattached is False, "'nothing said' is not 'said unattached'"


def test_an_unknown_role_names_the_ones_that_exist():
    with pytest.raises(traits.UnknownRole) as e:
        traits.default_catalog().of("advisor")
    assert "advisor" in str(e.value) and "worker" in str(e.value)
    assert "[roles.advisor]" in str(e.value), "and how to declare it"


def test_a_deployment_may_REDEFINE_a_builtin():
    c = traits.Catalog({"worker": {"attachment": "unattached"}})
    assert c.of("worker").unattached, "refusing this would make the seam decorative"


# --- tier: the unattached conditional ----------------------------------------

class _Reg:
    def __init__(self, agents):
        self._a = {a.name: a for a in agents}

    def get(self, n):
        if n not in self._a:
            raise LookupError(f"no such agent: {n}")
        return self._a[n]

    def all(self):
        return list(self._a.values())


def _catalog_with_advisor():
    return traits.Catalog({"advisor": {"attachment": "unattached",
                                       "workIntake": ["consulted"]}})


def test_an_unattached_role_needs_NO_reports_to():
    """The whole of #37 in one assertion: this used to be inexpressible."""
    reg = _Reg([Agent(name="stiwi", role="worker", reports_to=None)])
    plan = tier.plan_role_set(reg, "stiwi", "advisor", catalog=_catalog_with_advisor())
    assert [(a.name, a.role, a.reports_to) for a in plan.writes] == \
        [("stiwi", "advisor", None)]
    assert plan.routes == [], "not in the tree -> no stop routing to wire"


def test_an_unattached_role_REFUSES_reports():
    """Somebody asked for routing this role cannot carry. Quietly writing the card
    without it is how a tier becomes decorative."""
    reg = _Reg([Agent(name="stiwi", role="worker"),
                Agent(name="bond", role="worker")])
    with pytest.raises(ValueError, match="unattached"):
        tier.plan_role_set(reg, "stiwi", "advisor", reports=["bond"],
                           catalog=_catalog_with_advisor())


def test_an_undeclared_role_is_refused_with_the_list():
    reg = _Reg([Agent(name="stiwi", role="worker")])
    with pytest.raises(ValueError) as e:
        tier.plan_role_set(reg, "stiwi", "advisor")     # default catalog
    assert "declared roles" in str(e.value)


def test_the_builtin_three_behave_EXACTLY_as_the_enum_did():
    """The compatibility floor: a files-only deployment that declares nothing must
    not notice this change."""
    reg = _Reg([Agent(name="admin", role="administrator"),
                Agent(name="dee", role="lead", reports_to="admin"),
                Agent(name="bond", role="worker", reports_to="admin")])
    plan = tier.plan_role_set(reg, "dee", "lead", reports=["bond"])
    assert plan.routes == [("bond", "dee")]
    # depth 2 still holds: a worker under a lead cannot itself become a lead.
    deep = _Reg([Agent(name="admin", role="administrator"),
                 Agent(name="dee", role="lead", reports_to="admin"),
                 Agent(name="sub", role="worker", reports_to="dee")])
    with pytest.raises(ValueError, match="depth 3"):
        tier.plan_role_set(deep, "sub", "lead")


# --- roles --check must not call a declared role broken ----------------------

def test_an_unattached_agent_is_NOT_an_orphan():
    """Otherwise a declared role permanently fails the deployment's own health
    check — the feature ships and the checker says it is wrong."""
    reg = _Reg([Agent(name="admin", role="administrator"),
                Agent(name="stiwi", role="advisor", reports_to=None)])
    rep = roles_mod.check(reg, catalog=_catalog_with_advisor())
    row = next(r for r in rep.rows if r.agent == "stiwi")
    assert row.verdict == roles_mod.OK
    assert "unattached" in row.note, "and it says WHY it is not an orphan"


def test_a_worker_with_no_lead_is_STILL_an_orphan():
    """The negative control. If this ever passes, the check has been defanged and
    an agent whose stop events go nowhere reads as healthy."""
    reg = _Reg([Agent(name="admin", role="administrator"),
                Agent(name="bond", role="worker", reports_to=None)])
    rep = roles_mod.check(reg, catalog=_catalog_with_advisor())
    row = next(r for r in rep.rows if r.agent == "bond")
    assert row.verdict == roles_mod.BROKEN and "ORPHAN" in row.note


def test_an_AMBIGUOUS_role_is_treated_as_attached():
    """Any failure to answer resolves to strict. A false ORPHAN is noise an operator
    reads and dismisses; a falsely-excused worker hides an unreachable agent."""
    c = traits.Catalog({"keeper": {"scope": "domain-scoped"}})   # unranked conflict
    reg = _Reg([Agent(name="x", role="keeper", reports_to=None)])
    rep = roles_mod.check(reg, catalog=c)
    assert rep.rows[0].verdict == roles_mod.BROKEN


# --- the two sources ---------------------------------------------------------

def test_roles_declared_in_the_toml(tmp_path):
    (tmp_path / "shantytown.toml").write_text(
        '[roles.advisor]\nattachment = "unattached"\nwork_intake = ["consulted"]\n'
        '\n[precedence.scope]\n"domain-scoped" = 2\n"single-task" = 1\n')
    cfg = config.load(tmp_path)
    cat = cfg.catalog()
    assert cat.of("advisor").unattached
    assert cat.of("advisor").workIntake == frozenset({"consulted"}), \
        "snake_case is what an operator writes; camelCase is what the ontology calls it"
    assert cfg.precedence[("scope", "domain-scoped")] == 2


def test_a_typo_in_an_AXIS_NAME_is_refused(tmp_path):
    """A dropped `attachement` would leave an advisor silently inside the tree."""
    (tmp_path / "shantytown.toml").write_text(
        '[roles.advisor]\nattachement = "unattached"\n')
    with pytest.raises(config.ConfigError) as e:
        config.load(tmp_path)
    assert "attachement" in str(e.value) and "attachment" in str(e.value)


def test_a_non_axis_precedence_table_is_refused(tmp_path):
    (tmp_path / "shantytown.toml").write_text('[precedence.nope]\nx = 1\n')
    with pytest.raises(config.ConfigError, match="not a trait axis"):
        config.load(tmp_path)


def test_a_non_integer_rank_is_refused(tmp_path):
    (tmp_path / "shantytown.toml").write_text(
        '[precedence.scope]\n"domain-scoped" = "high"\n')
    with pytest.raises(config.ConfigError, match="integer rank"):
        config.load(tmp_path)


def test_no_declaration_means_the_builtin_three(tmp_path):
    assert config.load(tmp_path).catalog().known() == \
        ["administrator", "lead", "worker"]


# --- the graph source (pure projection; live shape pinned) -------------------

def test_derive_catalog_projects_the_graph_rows():
    """Rows shaped as the live graph answers them: one row per combination of the
    multi-valued axes, which is what OPTIONAL yields."""
    rows = [
        {"n": "worker", "attachment": "reports-to", "workIntake": "dispatched",
         "coordination": "neither", "scope": "single-task",
         "lifecycle": "persistent", "authority": "none"},
        {"n": "worker", "attachment": "reports-to", "workIntake": "self-directed",
         "coordination": "neither", "scope": "single-task",
         "lifecycle": "persistent", "authority": "none"},
        {"n": "advisor", "attachment": "unattached", "workIntake": "consulted",
         "coordination": "neither", "scope": "fleet-wide",
         "lifecycle": "persistent", "authority": "none"},
    ]
    # The live graph declares ranks for every single-valued axis, and it has to:
    # stacking these two also conflicts on `attachment`, which must be ranked or the
    # whole composition refuses. That is the design working, not a gap.
    prec = [{"ax": "scope", "v": "fleet-wide", "rank": 3},
            {"ax": "scope", "v": "single-task", "rank": 1},
            {"ax": "attachment", "v": "reports-to", "rank": 2},
            {"ax": "attachment", "v": "unattached", "rank": 1}]
    cat = quipu.derive_catalog(rows, prec)
    assert cat.of("worker").workIntake == frozenset({"dispatched", "self-directed"}), \
        "values accumulate across rows"
    assert cat.of("advisor").unattached
    assert cat.of(["worker", "advisor"]).scope == "fleet-wide", "ranked, not guessed"


def test_derive_catalog_ignores_a_malformed_rank():
    """A rank that is not a number ranks nothing — it must not become a 0 that
    silently wins or loses."""
    cat = quipu.derive_catalog(
        [{"n": "keeper", "scope": "domain-scoped"}],
        [{"ax": "scope", "v": "domain-scoped", "rank": "two"}])
    with pytest.raises(traits.AmbiguousTrait):
        cat.of(["worker", "keeper"])


def test_the_catalog_query_NAMES_the_axis_predicates():
    """Measured: the open-predicate form (?r ?p ?v) and the STRSTARTS filter each
    took 5.1s against the live graph — over the client's default 5s timeout — while
    naming the six takes 0.05s. The generality they bought was false anyway: Traits
    has six fields, so a seventh axis has nowhere to land."""
    q = quipu.catalog_query("https://example.invalid/o#")
    assert "STRSTARTS" not in q
    for axis in traits.AXES:
        assert f"trait{axis[0].upper()}{axis[1:]}" in q
    assert q.count("OPTIONAL") == len(traits.AXES)
