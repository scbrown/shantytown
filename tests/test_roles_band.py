"""`st roles --check` reports the SURVIVAL BAND the governor will resolve (aegis-upo93).

THE LOAD-BEARING TEST IS `test_the_roster_and_the_governor_AGREE_about_who_is_shed`.
Everything else is a specimen; that one is the invariant, and it is written against
`Verdict.excludes` on purpose — the roster is only useful here if it says the same
thing the throttle will do.

A `traits` tier spins down every agent whose band is below the one it spares. The
band is composed from the card's ROLE STACK through the catalog, so "will this
agent survive a throttle" was a question NO surface answered: at the tier that
engaged, sixteen of twenty cards were being shed and the only way to learn which
sixteen was to run the governor's resolution by hand against twenty JSON files.

The distinction this makes visible is one the deployment config already names in
its own words: an unbanded card and a card decided-`normal` resolve identically,
which makes "nobody wrote this one down" indistinguishable from "we chose this".
"""
from __future__ import annotations

import json
import time
import types
from pathlib import Path

from shantytown import governor as gov, roles
from shantytown.files import FilesRegistry
from shantytown.protocols import Agent


def _card(d: Path, name: str, **fields) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(fields))


class _Catalog:
    """Composes a role stack and ranks the survival axis — the minimum both
    `roles._band` and `governor.carries_any` need.

    THE ROLE NAME IS NOT THE BAND, and this stub models that rather than
    shortcutting it: the deployment declares `[roles.drains-last] survival =
    "last"`, so a card carrying `drains-last` resolves to `last`. A stub that
    matched names would let a name-matching bug pass — which is the very thing
    `carries_any` refuses to do in its own docstring.
    """

    precedence = {("survival", "normal"): 1, ("survival", "support"): 2,
                  ("survival", "last"): 3}
    DECLARED = {"normal": "normal", "support": "support", "drains-last": "last"}

    def of(self, roles_):
        if isinstance(roles_, str):
            roles_ = [roles_]
        bands = [self.DECLARED[r.lower()] for r in roles_
                 if r.lower() in self.DECLARED]
        survival = next((b for b in ("last", "support", "normal") if b in bands),
                        None)
        return types.SimpleNamespace(survival=survival, lane=[], unattached=False)


class _Blows:
    precedence: dict = {}

    def of(self, roles_):
        raise RuntimeError("unresolvable stack")


def _roster(tmp_path: Path) -> Path:
    c = tmp_path / "crew"
    _card(c, "sattler", role="administrator",
          roles=["administrator", "drains-last", "shantytown"])
    _card(c, "arnold", role="lead", reports_to="sattler",
          roles=["lead", "support", "monitoring"])
    _card(c, "tim", role="worker", reports_to="sattler",
          roles=["worker", "normal"])
    _card(c, "billy", role="worker", reports_to="sattler",
          roles=["worker", "graph"])            # nothing declares a band
    return c


# --- the invariant --------------------------------------------------------------


def test_the_roster_and_the_governor_AGREE_about_who_is_shed(tmp_path: Path):
    """THE test. For every card, the band the roster prints must predict what the
    throttle actually does to that card.

    Driven off `excludes` rather than off expected strings, because a roster that
    prints a plausible band while the governor sheds somebody else is the exact
    display-vs-enforcement disagreement this repo keeps paying for.
    """
    cat = _Catalog()
    c = _roster(tmp_path)
    rows = {r.agent: r for r in roles.check(FilesRegistry(c), catalog=cat).rows}
    agents = {a.name: a for a in FilesRegistry(c).all().exact()}

    tier = gov.Tier(at=80, window=gov.SEVEN_DAY, traits=("support",))
    v = gov.Verdict(reading=gov.Reading(pct=85, at=time.time()), tier=tier,
                    engaged=(tier,), by_window={gov.SEVEN_DAY: 85.0})

    for name, row in rows.items():
        shed = bool(v.excludes(agents[name], cat))
        spared = row.band in ("support", "last")
        assert spared is not shed, (
            f"the roster prints band: {row.band!r} for {name}, and the governor "
            f"{'SHEDS' if shed else 'SPARES'} them at the same tier")

    # Non-vacuity: this roster must actually contain both outcomes, or the loop
    # above proves nothing.
    assert {r.band for r in rows.values()} >= {"support", "last"}
    assert any(v.excludes(a, cat) for a in agents.values())


# --- the distinction the config asked for ---------------------------------------


def test_an_UNSET_band_is_distinguishable_from_a_chosen_normal(tmp_path: Path):
    """Both resolve to the normal band and both are shed. The point is that a
    REVIEWER can tell which one was a decision — the governor cannot."""
    rows = {r.agent: r.band
            for r in roles.check(FilesRegistry(_roster(tmp_path)),
                                 catalog=_Catalog()).rows}
    assert rows["tim"] == "normal"
    assert rows["billy"] == "normal (UNSET)"
    assert rows["arnold"] == "support"
    assert rows["sattler"] == "last"


def test_the_band_is_rendered_on_the_row(tmp_path: Path):
    out = roles.check(FilesRegistry(_roster(tmp_path)), catalog=_Catalog()).render()
    assert "band: support" in out
    assert "band: last" in out
    assert "band: normal (UNSET)" in out


# --- not measured, and could-not-tell, are different from any band ---------------


def test_no_catalog_prints_NO_band_rather_than_guessing_one(tmp_path: Path):
    """This checker does not print a word it did not measure — the same rule the
    hooks leg follows (`hooks: ?` rather than `hooks: ok`)."""
    rep = roles.check(FilesRegistry(_roster(tmp_path)))
    assert all(r.band == "" for r in rep.rows)
    assert "band:" not in rep.render()


def test_a_catalog_that_CANNOT_RESOLVE_says_the_agent_RUNS(tmp_path: Path):
    """Could-not-tell is not a band, and it must not render as one.

    `Verdict.excludes` FAILS OPEN on every could-not-tell, so this row is an
    agent that keeps running THROUGH a traits tier — the opposite of a shed.
    Printing a band here would say precisely the wrong thing.
    """
    cat, c = _Blows(), _roster(tmp_path)
    rep = roles.check(FilesRegistry(c), catalog=cat)
    assert all(r.band.startswith("?") for r in rep.rows)
    assert "runs" in rep.render()
    tier = gov.Tier(at=80, window=gov.SEVEN_DAY, traits=("support",))
    v = gov.Verdict(reading=gov.Reading(pct=85, at=time.time()), tier=tier,
                    engaged=(tier,), by_window={gov.SEVEN_DAY: 85.0})
    assert v.excludes(Agent(name="billy", role="worker",
                            roles=("worker", "graph")), cat) == "", (
        "the roster says could-not-tell while the governor sheds the agent")


def test_the_band_is_NOT_a_verdict(tmp_path: Path):
    """A band is a deployment's choice, never a fault. An all-`normal` roster
    about to be shed by a tier is still a healthy hierarchy, and a checker that
    reported it BROKEN would be unusable at exactly the moment it matters."""
    c = tmp_path / "crew"
    _card(c, "sattler", role="administrator", roles=["administrator"])
    _card(c, "tim", role="worker", reports_to="sattler", roles=["worker"])
    rep = roles.check(FilesRegistry(c), catalog=_Catalog())
    assert rep.verdict == roles.OK
    assert all(r.band == "normal (UNSET)" for r in rep.rows)
