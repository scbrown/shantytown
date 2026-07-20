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
            L.append(f"  COULD NOT TELL for {unknown}: a card or a hook file was "
                     "unreadable. This is NOT a clean result.")
        if not broken and not unknown:
            L.append(f"  {len(self.rows)} agents, every one reports somewhere.")
        return "\n".join(L)


def required_stop_directions(a: Agent, agents: list[Agent]) -> set[str]:
    """What stop directions THIS agent's position in the graph requires.

    PUBLIC because `st new` asks the same question at LAUNCH time (aegis-8p0j
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


def _live_verdict(a: Agent, agents: list[Agent], live) -> tuple[str, str]:
    """The THIRD leg (aegis-0v97): does the RUNNING PROCESS match the graph?

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
        # SAY WHAT IT HAS, NOT ONLY WHAT IT LACKS (dearing, aegis-0v97). The
        # first version of this said "carries NO stop hooks at all", which is
        # false as English and false in the expensive direction: the 8 agents it
        # named DO carry hooks — gastown's, including the rm -rf and force-push
        # tap guards — they simply carry no `stop_event` direction. Read
        # literally, the old string is aegis-05up ("respawn dropped --settings,
        # the guards are gone"), a genuine emergency that was NOT happening.
        # Whoever read it would either scramble for the wrong thing or start
        # disbelieving 05up for when it does fire. Naming the settings path also
        # makes the foreign launcher self-evident.
        carries = (f"stop directions {sorted(directions)}" if directions
                   else "no `stop_event` hook")
        whence = (f", its --settings is {wiring.settings_path}"
                  if wiring.settings_path
                  else ", and its launch line carries NO --settings at all "
                       "(this one IS the hookless-zombie case, cf. aegis-05up)")
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


def check(registry: Registry, emitted=None, live=None) -> Report:
    """Verify the hierarchy. Never raises for a bad card — that is a verdict.

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

    known = {a.name for a in agents}
    rows: list[Row] = []
    for a in sorted(agents, key=lambda x: x.name):
        if a.reports_to is None:
            if a.role == ROOT_ROLE:
                rows.append(Row(a.name, a.role, None, OK))
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
    return Report(rows)


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
