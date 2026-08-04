"""roles — the hierarchy, and `--check`, which asks whether it is real.

docs/cli.md: "Three outcomes: ok, broken, cannot tell. If it can't read a card it
says so and exits non-zero. A CHECKER THAT CAN ONLY REPORT HEALTH IS NOT A
CHECKER."

That line is not decoration. Every branch below is exercised by a test, because
a checker whose failure path has never run is indistinguishable from one that
cannot fail — and we have shipped several. The tests are the only evidence that
OK means anything.
"""
from __future__ import annotations

from dataclasses import dataclass

from .protocols import Agent, Registry

OK, BROKEN, CANNOT_TELL = "ok", "broken", "cannot tell"

# A fourth word, for the hooks leg ONLY, and it is not a verdict: it means the
# caller supplied no hook reader, so nothing was measured. It renders as
# `hooks: ?`. The old code printed `hooks: ok` here — a column header dressed as
# a finding (GitHub #6). Saying "unverified" costs nothing and lies about nothing.
UNVERIFIED = "unverified"

# An administrator reporting to nobody is the root, not an orphan. Anyone else
# with no lead has nowhere to send stop events, which is the whole point of the
# check.
ROOT_ROLE = "administrator"


@dataclass
class Row:
    agent: str
    role: str
    reports_to: str | None
    verdict: str
    note: str = ""
    hooks: str = UNVERIFIED   # the second leg; see check(emitted=...)
    live: str = UNVERIFIED    # the third leg; see check(live=...)
    # WHICH SURVIVAL BAND THE USAGE GOVERNOR WILL RESOLVE for this card
    # (aegis-upo93). "" = not measured (no catalog was passed), never a value.
    band: str = ""


@dataclass
class Report:
    rows: list[Row]

    @property
    def verdict(self) -> str:
        """Worst wins. `cannot tell` outranks `broken`: an unread card might be
        hiding either, and reporting the lesser one is the exit-2 bug again."""
        v = [r.verdict for r in self.rows]
        if CANNOT_TELL in v:
            return CANNOT_TELL
        if BROKEN in v:
            return BROKEN
        return OK

    def render(self) -> str:
        L = []
        for r in self.rows:
            rel = (f"reports_to: {r.reports_to}" if r.reports_to
                   else "reports_to: —")
            hooks = {OK: "hooks: ok", UNVERIFIED: "hooks: ?"}.get(r.hooks, "hooks: ok")
            # `live: ?` is common and legitimate (a DOWN pane cannot be read,
            # and route_stop already handles a down lead), so it is shown only
            # when it was actually measured — an unreadable pane must not read
            # as a finding.
            if r.live == OK:
                hooks += " live: ok"
            # THE BAND THAT DECIDES WHETHER THIS CARD SURVIVES A THROTTLE
            # (aegis-upo93). Shown on every measured row, including the ordinary
            # ones, because the question a reader brings to this table under a
            # traits tier is "who is still running" and the answer must not
            # require reading twenty JSON files. Absent = not measured.
            if r.band:
                hooks += f" band: {r.band}"
            tail = {OK: hooks,
                    BROKEN: f"*** {r.note or 'BROKEN'} ***",
                    CANNOT_TELL: f"*** CANNOT TELL: {r.note} ***"}[r.verdict]
            L.append(f"  {r.agent:<11} {r.role:<14} {rel:<24} {tail}")

        broken = sum(1 for r in self.rows if r.verdict == BROKEN)
        unknown = sum(1 for r in self.rows if r.verdict == CANNOT_TELL)
        L.append("")
        if broken:
            L.append(f"  BLOCKED: {broken} agent's stop events go nowhere."
                     if broken == 1 else
                     f"  BLOCKED: {broken} agents' stop events go nowhere.")
        if unknown:
            # Never let this render as a pass. It is the reason exit 2 exists.
            #
            # It used to assert the CAUSE here — "a card or a hook file was
            # unreadable" — which was already wrong for a dangling lead (nothing was
            # unreadable; the registry answered fine) and is wrong again for an
            # unvouched-for EMPTY registry. Guessing a cause in the summary is the
            # small version of the same sin the rest of this module is about: every
            # row already prints its own note, which is the measured reason. The
            # summary's whole job is to refuse to be read as a pass.
            L.append(f"  COULD NOT TELL for {unknown}. This is NOT a clean result "
                     "— see the reason on each row above.")
        if not broken and not unknown:
            L.append(f"  {len(self.rows)} agents, every one reports somewhere.")
        return "\n".join(L)


def required_stop_directions(a: Agent, agents: list[Agent]) -> set[str]:
    """What stop directions THIS agent's position in the graph requires.

    PUBLIC because `st new` asks the same question at LAUNCH time (internal-ref
    gap 1) that `--check` asks after the fact. There must be exactly ONE
    definition of "what does this agent need": if the launcher and the checker
    computed it separately, a disagreement between them would be unattributable —
    you could not tell a real drift from two implementations of the graph rule.
    That is the identical argument runtime.py makes for its two settings parsers.
    """
    need = set()
    if a.reports_to is not None:
        need.add("send")
    if any(o.reports_to == a.name for o in agents):
        need.add("drain")
    return need


_needs = required_stop_directions       # the in-module name, unchanged


def live_verdict(a: Agent, agents: list[Agent], live) -> tuple[str, str]:
    """The THIRD leg (internal-ref): does the RUNNING PROCESS match the graph?

    PUBLIC for the same reason required_stop_directions is: it now has a second
    consumer. `st crew` asks this continuously, `st roles --check` asks it on
    demand, and if the two computed it separately a disagreement between them
    would be unattributable — you could not tell real drift from two
    implementations of one rule. One definition, two callers.

    Leg two asks whether the ROLE'S ARTIFACT carries the right hooks. That is a
    strictly weaker question, and the gap between them is not theoretical — it
    was live on this store for the whole time leg two existed:

        dearing   role=lead   lead.settings.json emits [send, drain]   hooks: ok
                  ...launched by gt-crew-up with gastown settings carrying no
                  stop_event hook at all. Seven workers routed to it. Every one
                  of their stop events was write-only, and --check was GREEN.

    An artifact is a statement of intent. `st` does not own every process that
    answers to a name in its registry, so intent is not evidence. tmux.py states
    the same rule for the kill path — a pane NAME match is never sufficient
    permission to reap. This is that rule for liveness.

    A DOWN pane is NOT a fault here: route_stop already rises to the
    administrator when a lead is unreachable, loudly and with a reason. The
    fault this leg exists to catch is the one that path cannot see — pane UP,
    wiring WRONG, so nothing rises and nothing drains.
    """
    wiring = live(a.pane) if a.pane else None
    if wiring is None:
        # Pane down, or nothing readable. Not a pass and not a failure: say so.
        return UNVERIFIED, ""
    directions = wiring.directions
    need = _needs(a, agents)
    missing = need - directions
    if missing:
        # SAY WHAT IT HAS, NOT ONLY WHAT IT LACKS (dearing, internal-ref). The
        # first version of this said "carries NO stop hooks at all", which is
        # false as English and false in the expensive direction: the 8 agents it
        # named DO carry hooks — gastown's, including the rm -rf and force-push
        # tap guards — they simply carry no `stop_event` direction. Read
        # literally, the old string is internal-ref ("respawn dropped --settings,
        # the guards are gone"), a genuine emergency that was NOT happening.
        # Whoever read it would either scramble for the wrong thing or start
        # disbelieving 05up for when it does fire. Naming the settings path also
        # makes the foreign launcher self-evident.
        carries = (f"stop directions {sorted(directions)}" if directions
                   else "no `stop_event` hook")
        whence = (f", its --settings is {wiring.settings_path}"
                  if wiring.settings_path
                  else ", and its launch line carries NO --settings at all "
                       "(this one IS the hookless-zombie case)")
        # Name EVERY consequence, not the first one. A lead missing both legs
        # strands its reports as well as itself, and reporting only "its own
        # stop dies here" would understate it by seven agents.
        because = []
        if "send" in missing:
            because.append("its own stop dies here")
        if "drain" in missing:
            n = sum(1 for o in agents if o.reports_to == a.name)
            because.append(f"the stops of its {n} report(s) land in a store "
                           "nothing reads")
        why = " AND ".join(because)
        return BROKEN, ("LIVE PROCESS DOES NOT MATCH THE GRAPH: the process in "
                        f"pane {a.pane!r} carries {carries}{whence}, but this "
                        f"agent needs {sorted(need)} — so {why}")
    return OK, ""


_live_verdict = live_verdict   # the in-module name, unchanged


def _hooks_verdict(a: Agent, agents: list[Agent], emitted) -> tuple[str, str]:
    """The SECOND leg (GitHub #6.4): do the emitted stop hooks match the graph?

    The graph — not the role name — states the requirement, because the graph is
    what the events actually have to travel along:

        it reports to someone      -> its hooks must SEND  (or its stop dies here)
        someone reports to it      -> its hooks must DRAIN (or its reports' stops
                                      land in a store nothing ever reads)

    A role whose settings file is missing or unreadable is CANNOT TELL, never ok.
    """
    directions = emitted(a.role)
    if directions is None:
        return CANNOT_TELL, f"no readable stop hooks emitted for role {a.role!r}"
    need = _needs(a, agents)
    missing = need - directions
    if missing:
        return BROKEN, ("HOOKS DO NOT MATCH THE GRAPH: role "
                        f"{a.role!r} emits {sorted(directions) or 'nothing'}, "
                        f"but this agent needs {sorted(need)}")
    return OK, ""


def _no_empty_note() -> str:
    """The fail-safe default for a registry that does not implement `empty_note`.

    Returning a NOTE (not None) is the point: an unknown registry has not vouched
    for its empty answer, so its empty answer is not trusted. See
    Registry.empty_note for why the asymmetry runs this way.
    """
    return ("this registry does not say whether an empty answer means 'nobody "
            "exists' or 'I looked in the wrong place', so it is not read as a "
            "clean result")


def check(registry: Registry, emitted=None, live=None, catalog=None) -> Report:
    """Verify the hierarchy. Never raises for a bad card — that is a verdict.

    `catalog` (traits.Catalog) is how a role gets to NOT BE IN THE TREE (GitHub
    #37). Without it, "no reports_to and not the administrator" is an ORPHAN, which
    is right for a worker and wrong for an advisor: an unattached role has no lead
    by definition, and reporting it broken would make a declared role permanently
    fail the deployment's own health check. That is the shape of bug this repo calls
    exists-not-acts — the feature ships, and the checker says it is wrong.
    Omitted = the built-in three, where every non-root really does need a lead.

    `emitted` is an optional reader `role -> set[str] | None` giving the stop
    directions the role's EMITTED hook artifact actually carries (see
    runtime.emitted_stop_directions). Pass it and `hooks: ok` becomes an
    observation; omit it and the hooks column is reported as UNVERIFIED rather
    than ok — this checker does not get to print a word it did not measure.

    `live` is an optional reader `pane -> set[str] | None` giving the stop
    directions the RUNNING PROCESS in that pane actually carries (see
    runtime.live_stop_directions). Same contract, strictly stronger question:
    `emitted` verifies the role's INTENT, `live` verifies the running REALITY.
    They can disagree, and when they do the tier is broken while the artifact
    looks perfect — see _live_verdict for the measured case that motivated it.
    """
    try:
        agents: list[Agent] = registry.all()
    except Exception as e:
        # The registry itself is unreachable. Not "everyone is fine".
        return Report([Row("(registry)", "—", None, CANNOT_TELL, str(e))])

    # NOBODY IS NOT A CLEAN BILL OF HEALTH unless the registry vouches for it.
    #
    # An empty Report's verdict is OK, because "worst wins" over no rows is OK.
    # That is the empty-Report false pass, and it survived here long after it was
    # closed elsewhere: test_doctor.py's docstring already cites `roles --check`
    # printing "0 agents, every one reports somewhere" and exiting 0 as the
    # canonical example of a checker that can only report health. That instance was
    # fixed in FilesRegistry.all (absent dir raises). This is the other one, and
    # `--registry quipu` reached it: 12 real CrewMembers in the graph, zero rows
    # back under a wrong namespace, exit 0.
    #
    # The verdict belongs to the REGISTRY, not here — see Registry.empty_note. For
    # files, an empty answer is a complete observation and stays OK; for a graph it
    # cannot be, and says so. This function stays registry-agnostic, which is the
    # "quipu has not leaked into the core" guarantee.
    #
    # getattr, not a direct call: several tests hand this function a minimal
    # duck-typed registry, and the fail-safe default belongs in ONE place. A
    # registry that does not answer the question is not treated as having answered
    # it "no".
    if not agents:
        note = getattr(registry, "empty_note", _no_empty_note)()
        if note is not None:
            return Report([Row("(registry)", "—", None, CANNOT_TELL, note)])

    known = {a.name for a in agents}
    rows: list[Row] = []
    for a in sorted(agents, key=lambda x: x.name):
        if a.reports_to is None:
            if a.role == ROOT_ROLE:
                rows.append(Row(a.name, a.role, None, OK))
            elif _unattached(catalog, a.role):
                # Not in the tree, so having no lead is its DEFINITION, not a fault.
                # Named rather than left blank: a reader scanning for orphans needs
                # to see why this row is not one.
                rows.append(Row(a.name, a.role, None, OK, "unattached by role"))
            else:
                rows.append(Row(a.name, a.role, None, BROKEN, "ORPHAN"))
        elif a.reports_to not in known:
            rows.append(Row(a.name, a.role, a.reports_to, CANNOT_TELL,
                            f"lead {a.reports_to!r} is not in the registry"))
        elif a.reports_to == a.name:
            rows.append(Row(a.name, a.role, a.reports_to, BROKEN,
                            "REPORTS TO ITSELF"))
        else:
            rows.append(Row(a.name, a.role, a.reports_to, OK))

        # Both extra legs run even for a row the line-check already failed: an
        # ORPHAN with broken hooks has two problems and should say so.
        if emitted is None:
            rows[-1].hooks = UNVERIFIED
        else:
            hv, note = _hooks_verdict(a, agents, emitted)
            rows[-1].hooks = hv
            _fold(rows[-1], hv, note)

        if live is None:
            rows[-1].live = UNVERIFIED
        else:
            lv, note = _live_verdict(a, agents, live)
            rows[-1].live = lv
            _fold(rows[-1], lv, note)

        # NOT A VERDICT, and deliberately not folded into one. A band is a
        # deployment's choice, never a fault — this leg reports, it does not judge.
        rows[-1].band = _band(catalog, a)
    return Report(rows)


def _band(catalog, a: Agent) -> str:
    """The survival band the USAGE GOVERNOR will resolve for this card, as the
    text to print. "" when nothing was measured.

    WHY THE ROSTER SHOWS THIS AT ALL (aegis-upo93). A `traits` tier spins down
    every agent whose band is below the one it spares, and the band is composed
    from the card's ROLE STACK through the catalog — so "will this agent survive
    a throttle" was a question no surface answered and every operator had to
    reconstruct from JSON. At the tier that engaged, sixteen of twenty cards were
    being shed and the only way to learn which sixteen was to run the governor's
    own resolution by hand.

    `normal (UNSET)` IS THE POINT OF THIS FUNCTION, not a formatting detail. The
    deployment config says it in its own words: an unbanded card and a card
    decided-`normal` resolve identically, which makes "nobody wrote this one
    down" indistinguishable from "we chose this" — the governor cannot tell, and
    neither can a reviewer. This is the surface where a reviewer can.

    `?` IS COULD-NOT-TELL, and it is a different answer from any band. The
    governor FAILS OPEN on every could-not-tell (Verdict.excludes), so a `?` row
    is an agent that keeps running through a traits tier — the opposite of a
    shed. Rendering it as a band would say precisely the wrong thing.
    """
    if catalog is None:
        return ""
    roles = tuple(getattr(a, "effective_roles", lambda: ())() or ())
    if not roles:
        return ""
    try:
        composed = catalog.of(list(roles))
    except Exception:      # noqa: BLE001 — UnknownRole / AmbiguousTrait / anything
        return "? (the governor fails OPEN — this agent runs)"
    from .traits import SURVIVAL
    return str(getattr(composed, SURVIVAL, None) or "normal (UNSET)")


def _unattached(catalog, role: str) -> bool:
    """Does the deployment say this role is outside the tree?

    Any failure to answer is FALSE — an undeclared role, a catalog that cannot
    resolve a stacked conflict, no catalog at all. The safe direction is the strict
    one: mistakenly calling an advisor an orphan is a noisy false positive an
    operator can read and dismiss, while mistakenly excusing a real worker with no
    lead hides an agent whose stop events go nowhere, silently. That is the failure
    this whole check exists to catch.
    """
    if catalog is None:
        return False
    try:
        return catalog.of(role).unattached
    except Exception:      # noqa: BLE001 — UnknownRole / AmbiguousTrait / anything
        return False


def _fold(row: Row, verdict: str, note: str) -> None:
    """Merge one leg's verdict into the row. A worse verdict wins, and a second
    problem NEVER hides behind the first — both notes are kept."""
    if verdict in (OK, UNVERIFIED):
        return
    if row.verdict == OK:
        row.verdict, row.note = verdict, note
        return
    row.note = f"{row.note}; also {note}"
    if verdict == CANNOT_TELL:
        row.verdict = CANNOT_TELL
