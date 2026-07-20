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
    need = set()
    if a.reports_to is not None:
        need.add("send")
    if any(o.reports_to == a.name for o in agents):
        need.add("drain")
    missing = need - directions
    if missing:
        return BROKEN, ("HOOKS DO NOT MATCH THE GRAPH: role "
                        f"{a.role!r} emits {sorted(directions) or 'nothing'}, "
                        f"but this agent needs {sorted(need)}")
    return OK, ""


def check(registry: Registry, emitted=None) -> Report:
    """Verify the hierarchy. Never raises for a bad card — that is a verdict.

    `emitted` is an optional reader `role -> set[str] | None` giving the stop
    directions the role's EMITTED hook artifact actually carries (see
    runtime.emitted_stop_directions). Pass it and `hooks: ok` becomes an
    observation; omit it and the hooks column is reported as UNVERIFIED rather
    than ok — this checker does not get to print a word it did not measure.
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

        if emitted is None:
            rows[-1].hooks = UNVERIFIED
            continue
        # The hooks leg runs even for a row the line-check already failed: an
        # ORPHAN with broken hooks has two problems and should say so. A worse
        # hooks verdict wins the row.
        hv, note = _hooks_verdict(a, agents, emitted)
        rows[-1].hooks = hv
        if hv == OK:
            continue
        if rows[-1].verdict == OK:
            rows[-1].verdict, rows[-1].note = hv, note
        else:
            # Already failing the line check. Do NOT let the first problem hide
            # the second — both go in the note, and the worse verdict wins.
            rows[-1].note = f"{rows[-1].note}; also {note}"
            if hv == CANNOT_TELL:
                rows[-1].verdict = CANNOT_TELL
    return Report(rows)
