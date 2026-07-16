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
            tail = {OK: "hooks: ok",
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
            L.append(f"  COULD NOT TELL for {unknown}: a card was unreadable. "
                     "This is NOT a clean result.")
        if not broken and not unknown:
            L.append(f"  {len(self.rows)} agents, every one reports somewhere.")
        return "\n".join(L)


def check(registry: Registry) -> Report:
    """Verify the hierarchy. Never raises for a bad card — that is a verdict."""
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
    return Report(rows)
