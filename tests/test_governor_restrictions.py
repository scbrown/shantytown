"""The display must name EVERY engaged restriction, not one (aegis-upo93).

THE LOAD-BEARING TEST IS `test_an_agent_the_governor_BANS_is_named_in_the_display`.
Everything else here is a specimen; that one is the invariant, and it is the one
that stays true when a third KIND of restriction is added.

aegis-yc864 fixed two answers to one question — `floor` took the strictest floor
across every engaged tier while the status line rendered `engaged[-1]`. `governing`
was the fix and it is correct. But `governing` answers "WHICH FLOOR", and returns
ONE tier because floors compose into one number. A trait tier restricts WHO RUNS,
which is a restriction of a different KIND engaged at the same time — so a display
built on `governing` alone can only ever answer one of two live questions, and the
one it drops is the one that stops agents.

Measured live 2026-08-03, seven_day at 79% with both kinds engaged:

    st crew --governor
      ok 3/50/2877 79/90/118076 dispatch only P0 and above [seven_day >= 65%]

`Verdict.excludes` was simultaneously refusing to let most of the roster run. The
coordinator read that line, concluded the priority floor was the only restriction
in force, and moved to restore an agent the governor had banned. Nothing on the
line could have told them otherwise — which is why the invariant below is written
against `excludes` rather than against an expected string: a restriction that
CHANGES AN OUTCOME must appear in the text a human reads.
"""
from __future__ import annotations

import types

import pytest

import shantytown.cli as cli
import shantytown.governor as gov_mod
from shantytown.governor import DRAIN, FIVE_HOUR, SEVEN_DAY, Reading, Tier, Verdict
from shantytown.protocols import Agent


# The live deployed shape that produced the bug.
SEVEN_50 = Tier(at=50, window=SEVEN_DAY, min_priority=1)
SEVEN_70 = Tier(at=70, window=SEVEN_DAY, min_priority=0)
SEVEN_80_SUPPORT = Tier(at=80, window=SEVEN_DAY, traits=("support",))
SEVEN_95_DRAIN = Tier(at=95, window=SEVEN_DAY, action=DRAIN)
FIVE_50 = Tier(at=50, window=FIVE_HOUR, min_priority=1)
FIVE_80_ONCALL = Tier(at=80, window=FIVE_HOUR, traits=("oncall",))


def _verdict(*tiers: Tier) -> Verdict:
    return Verdict(reading=Reading(), tier=tiers[-1] if tiers else None,
                   engaged=tuple(tiers))


class _Catalog:
    """The minimum a `carries_any` resolution needs: compose a role stack, and
    rank the survival axis. Hand-built rather than loaded from TOML so this file
    pins the DISPLAY, not the config loader."""

    precedence = {("survival", "normal"): 1, ("survival", "support"): 2,
                  ("survival", "last"): 3}

    def of(self, roles):
        carried = {r.lower() for r in roles}
        survival = None
        for band in ("last", "support", "normal"):
            if band in carried:
                survival = band
                break
        return types.SimpleNamespace(survival=survival,
                                     lane=[r for r in carried
                                           if r in {"oncall", "monitoring"}])


CAT = _Catalog()


# --- the invariant --------------------------------------------------------------


@pytest.mark.parametrize("tiers", [
    (SEVEN_50, SEVEN_70, SEVEN_80_SUPPORT),      # the live specimen
    (SEVEN_80_SUPPORT, SEVEN_50, SEVEN_70),      # order must not matter
    (SEVEN_80_SUPPORT,),                         # a trait tier with no floor at all
    (FIVE_50, SEVEN_80_SUPPORT),                 # the two kinds on DIFFERENT budgets
    (SEVEN_50, SEVEN_80_SUPPORT, FIVE_80_ONCALL),  # two bands, two windows
])
def test_an_agent_the_governor_BANS_is_named_in_the_display(tiers):
    """THE test. If `excludes` stops an agent, the line a human reads says so.

    Stated over tier sets and driven off `excludes` rather than off an expected
    string, because the failure was never wrong text — it was a display computed
    from a property that structurally could not see the second restriction.
    """
    v = _verdict(*tiers)
    banned = Agent(name="tim", role="worker", roles=("worker", "normal"))
    why = v.excludes(banned, CAT)
    assert why, "specimen error: this tier set does not actually ban anyone"

    shown = "; ".join(t.label() for t in v.restrictions)
    for tier in v.trait_tiers:
        assert f"{'/'.join(tier.traits)} crew runs" in shown, (
            f"{tier.window} >= {tier.at}% bans {banned.name} from RUNNING and the "
            f"display never mentions it: {shown!r}")
        assert f"{tier.window} >= {tier.at}%" in shown, (
            f"the restriction is named without its budget: {shown!r}")


@pytest.mark.parametrize("tiers", [
    (SEVEN_50, SEVEN_70, SEVEN_80_SUPPORT),
    (FIVE_50, SEVEN_80_SUPPORT),
    (SEVEN_50, SEVEN_70),
])
def test_the_floor_is_STILL_named_and_still_agrees_with_enforcement(tiers):
    """aegis-yc864's invariant must survive this change: whatever `admits`
    enforces is what the human is shown. Adding a second restriction to the line
    must not displace the first."""
    v = _verdict(*tiers)
    assert v.floor is not None
    shown = "; ".join(t.label() for t in v.restrictions)
    assert f"dispatch only P{v.floor} and above" in shown, shown


def test_ORDER_of_engaged_does_not_change_the_answer():
    a = _verdict(SEVEN_50, SEVEN_70, SEVEN_80_SUPPORT).restrictions
    b = _verdict(SEVEN_80_SUPPORT, SEVEN_70, SEVEN_50).restrictions
    assert set(a) == set(b)


# --- non-vacuity: the shipped code must FAIL these ------------------------------


def test_the_governing_ONLY_display_would_have_been_WRONG_here():
    """A regression test the buggy implementation also passes is not one.

    This reproduces the shipped line character-for-character on the live tier
    shape and asserts it omits the ban.
    """
    v = _verdict(SEVEN_50, SEVEN_70, SEVEN_80_SUPPORT)
    old = v.governing.label()                     # what the CLI used to print
    assert old == "dispatch only P0 and above [seven_day >= 70%]"
    assert "support" not in old, "specimen error: the old label already said it"
    assert v.excludes(Agent(name="tim", role="worker", roles=("worker", "normal")),
                      CAT), "specimen error: nobody was banned"
    new = "; ".join(t.label() for t in v.restrictions)
    assert new != old and "only support crew runs" in new


# --- the shapes that must NOT grow a second clause ------------------------------


def test_a_drain_collapses_to_itself():
    """Under a FULL STOP nothing dispatches and nobody runs at any band, so
    listing "only support crew runs" beside it would describe a distinction the
    drain has already erased — and would read as "support crew still runs",
    which is the expensive direction to be wrong in. Mirrors `effect()`."""
    v = _verdict(SEVEN_50, SEVEN_80_SUPPORT, SEVEN_95_DRAIN)
    assert v.restrictions == (SEVEN_95_DRAIN,)
    shown = "; ".join(t.label() for t in v.restrictions)
    assert "FULL STOP" in shown
    assert "crew runs" not in shown, shown


def test_no_engaged_tiers_is_an_EMPTY_label_not_an_invented_one():
    """The ungoverned case is a real state, not an error."""
    assert _verdict().restrictions == ()


def test_a_floor_only_verdict_is_unchanged():
    """No trait tier engaged -> exactly the old single-clause line. This change
    must be invisible on the fleet's ordinary shape."""
    v = _verdict(SEVEN_50, SEVEN_70)
    assert v.restrictions == (SEVEN_70,)
    assert "; ".join(t.label() for t in v.restrictions) == v.governing.label()


def test_a_tier_that_is_BOTH_floor_and_trait_appears_once():
    both = Tier(at=80, window=SEVEN_DAY, min_priority=0, traits=("support",))
    v = _verdict(both)
    assert v.restrictions == (both,)


# --- end to end, through the command a status bar actually runs -----------------


class _Reader:
    def __init__(self, readings):
        self._readings = readings

    def read_all(self):
        return self._readings

    def read(self):
        return self._readings.get(FIVE_HOUR)


class _Gov:
    def __init__(self, readings, verdict):
        self.reader = _Reader(readings)
        self._verdict = verdict
        self.policy = gov_mod.Policy(tiers=(SEVEN_50, SEVEN_70, SEVEN_80_SUPPORT,
                                            SEVEN_95_DRAIN, FIVE_50))

    def evaluate(self, *, persist=True):
        assert persist is False, "the status-bar read must not extend a hold"
        return self._verdict


def test_st_crew_governor_prints_BOTH_restrictions(monkeypatch, capsys):
    """The whole bug, at the surface it was measured on."""
    g = _Gov({FIVE_HOUR: Reading(pct=3, source="stub"),
              SEVEN_DAY: Reading(pct=79, source="stub")},
             _verdict(SEVEN_50, SEVEN_70, SEVEN_80_SUPPORT))
    monkeypatch.setattr(cli, "_governor", lambda a: g)
    monkeypatch.setattr(cli.creel_advisory_mod, "controller_line",
                        lambda *a, **k: "governor recommends +2")
    rc = cli._crew_governor(types.SimpleNamespace(root="/nonexistent"))
    # The indented UTIL line beneath is aegis-967a9's; the CAPACITY line's parse
    # contract is what this test is about and is deliberately unchanged.
    out = "\n".join(l for l in capsys.readouterr().out.strip().splitlines()
                    if not l.strip().startswith("UTIL[")).strip()
    assert rc == cli.OK
    assert out == ("ok 3/50/- 79/80/- dispatch only P0 and above "
                   "[seven_day >= 70%]; only support crew runs [seven_day >= 80%] "
                   "| governor recommends +2")
    # THE PARSE CONTRACT IS UNCHANGED: a status bar takes three fields and treats
    # the remainder as free text. `; ` inside the label must not break that.
    status, five, seven, label = out.split(" ", 3)
    assert (status, five, seven) == ("ok", "3/50/-", "79/80/-")
    assert label.count(";") == 1
