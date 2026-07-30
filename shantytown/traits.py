"""traits — a role is a SET OF TRAITS the deployment declares, not an enum st ships.

WHY (GitHub #37, and the fourth layer #13 closed without).

`tier.py` had `VALID_ROLES = ("worker", "lead", "administrator")` and validated
against it. That is not just a vocabulary: `role == tree-position` was baked in, so
the reports-to rules applied to EVERY role, and any role that is not in the tree was
inexpressible. An advisor — consulted on demand, reporting to nobody, with nobody
reporting to it — failed validation for having no `reports_to`. So did an observer,
and a relay. The only way to add one was to edit this package.

THE UNLOCK IS ONE CONDITIONAL: if a role's attachment is `unattached`, the
reports-to rules do not apply to it. Everything else follows from traits rather than
from a name, which is what lets a deployment add a role shape without a code change.

SIX AXES, and which are single- vs multi-valued is load-bearing. The enum failed
precisely because two of them are MULTI: a worker is dispatched AND self-directed; a
lead absorbs AND dispatches. Collapsing those to one label is what made "a lead is a
worker that also absorbs" a comment rather than a fact the code could read.

    attachment    SINGLE   rooted | reports-to | unattached
    workIntake    MULTI    dispatched | self-directed | consulted | escalations-only | none
    coordination  MULTI    absorbs | dispatches | neither
    scope         SINGLE   fleet-wide | domain-scoped | single-task
    lifecycle     SINGLE   persistent | ephemeral
    authority     SINGLE   veto-in-scope | gate | none

ROLES STACK, and composing them is a DECLARED FUNCTION, not a judgement: multi axes
union; single axes take the highest declared PRECEDENCE. st computes that function.
It does not decide policy — the ranks are data, supplied by the same deployment that
supplies the roles.

AND WHERE THERE IS NO RANK, IT REFUSES (AmbiguousTrait). This is the one rule not to
soften. A stacked {worker, keeper} is `scope = {single-task, domain-scoped}` and
`authority = {none, veto-in-scope}` at once. The design prose says "most-specific
wins" — but prose is not data, and a tie-break written into this module would be the
closed-enum problem reappearing one layer up, which is the exact thing the trait
model set out to kill. So an unranked conflict is an error naming the axis, the
values, and the fact that the deployment has to rank them.

ONLY THREE ROLES ARE BUILT IN, deliberately: the three the built-in PROCESS is
defined for. `tier.py` implements depth-2, a-lead-absorbs, and rise-past-a-dead-lead
for exactly those, and shipping a catalog of ten here would re-hardcode in Python
the thing the deployment is supposed to declare. Anything beyond the three comes
from the deployment — `[roles.<name>]` in shantytown.toml, or the graph.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

SINGLE = "single"
MULTI = "multi"

# The axis names are the ONTOLOGY's own (`traitWorkIntake` -> `workIntake`), not a
# re-spelling. Two sources declare these — a config file and a graph — and a
# translation layer between them is where they would silently disagree about which
# axis a value belongs to.
AXES: dict[str, str] = {
    "attachment": SINGLE,
    "workIntake": MULTI,
    "coordination": MULTI,
    "scope": SINGLE,
    "lifecycle": SINGLE,
    "authority": SINGLE,
}

# snake_case is what an operator writes in TOML; camelCase is what the ontology
# calls it. Accepted as aliases rather than refused, because a papercut refusal on
# the more natural spelling teaches nothing — but ONE canonical name is stored, so
# the two sources cannot end up describing the same axis twice.
_ALIASES = {"work_intake": "workIntake"}

UNATTACHED = "unattached"


class UnknownRole(ValueError):
    """A role nothing describes — not built in, not declared by the deployment."""


class AmbiguousTrait(ValueError):
    """A stacked role set conflicts on a single-valued axis and nothing ranks it."""


@dataclass(frozen=True)
class Traits:
    """One agent's EFFECTIVE traits, composed across its role set.

    None on a single axis means NOTHING DECLARED IT — which is not the same as a
    default. A partial role is legal and real: the live `escalation-target` declares
    a work intake and no attachment at all, because attachment is not what that role
    is about. Inventing a value here would put an agent in the tree on the strength
    of a role that never mentioned the tree.
    """
    attachment: str | None = None
    workIntake: frozenset[str] = frozenset()
    coordination: frozenset[str] = frozenset()
    scope: str | None = None
    lifecycle: str | None = None
    authority: str | None = None

    @property
    def unattached(self) -> bool:
        """The conditional this whole module exists for. Note it is FALSE when
        attachment is None: 'nothing said' is not 'said unattached', and a role set
        that never mentions attachment must not silently escape the tier's rules."""
        return self.attachment == UNATTACHED

    @property
    def absorbs(self) -> bool:
        return "absorbs" in self.coordination

    @property
    def dispatches(self) -> bool:
        return "dispatches" in self.coordination


# The three the built-in process is defined for, and no more (see the docstring).
# Values are the live catalog's, not invented here.
BUILTIN: dict[str, dict[str, tuple[str, ...]]] = {
    "worker": {
        "attachment": ("reports-to",),
        "workIntake": ("dispatched", "self-directed"),
        "coordination": ("neither",),
        "scope": ("single-task",),
        "lifecycle": ("persistent",),
        "authority": ("none",),
    },
    "lead": {
        "attachment": ("reports-to",),
        "workIntake": ("dispatched", "self-directed"),
        "coordination": ("absorbs", "dispatches"),
        "scope": ("single-task",),
        "lifecycle": ("persistent",),
        "authority": ("none",),
    },
    "administrator": {
        "attachment": ("rooted",),
        "workIntake": ("dispatched", "self-directed"),
        "coordination": ("absorbs", "dispatches"),
        "scope": ("fleet-wide",),
        "lifecycle": ("persistent",),
        "authority": ("none",),
    },
}


def canonical_axis(name: str) -> str:
    return _ALIASES.get(name, name)


class Catalog:
    """Role definitions from every source, merged. A deployment's declaration wins
    over the built-in of the same name — that is what a declaration is for, and
    refusing to let a deployment redefine `worker` would make the seam decorative.

    `precedence` is keyed (axis, value) -> int; higher wins. Absent = unranked, and
    an unranked CONFLICT refuses rather than guessing (see AmbiguousTrait).
    """

    def __init__(self, roles: Mapping[str, Mapping[str, Iterable[str]]] | None = None,
                 precedence: Mapping[tuple[str, str], int] | None = None,
                 *, builtin: bool = True):
        self.roles: dict[str, dict[str, tuple[str, ...]]] = {}
        if builtin:
            for name, axes in BUILTIN.items():
                self.roles[name] = dict(axes)
        for name, axes in (roles or {}).items():
            self.roles[name] = {canonical_axis(k): tuple(_as_list(v))
                                for k, v in axes.items()}
        self.precedence = dict(precedence or {})

    def describes(self, role: str) -> bool:
        return role in self.roles

    def known(self) -> list[str]:
        return sorted(self.roles)

    def of(self, roles: Sequence[str] | str) -> Traits:
        """Compose the trait set for one role or a stack of them."""
        names = [roles] if isinstance(roles, str) else list(roles)
        if not names:
            return Traits()
        for n in names:
            if n not in self.roles:
                raise UnknownRole(
                    f"unknown role {n!r}. Declared roles: "
                    f"{', '.join(self.known()) or '(none)'}. Add it as "
                    f"[roles.{n}] in shantytown.toml, or declare it in the graph "
                    f"— st no longer ships a closed list.")
        gathered: dict[str, list[str]] = {axis: [] for axis in AXES}
        for n in names:
            for axis, values in self.roles[n].items():
                if axis not in AXES:
                    continue                      # validated at parse time, not here
                for v in values:
                    if v not in gathered[axis]:
                        gathered[axis].append(v)
        out: dict[str, object] = {}
        for axis, kind in AXES.items():
            values = gathered[axis]
            if kind == MULTI:
                out[axis] = frozenset(values)
            else:
                out[axis] = self._one(axis, values, names)
        return Traits(**out)                      # type: ignore[arg-type]

    def _one(self, axis: str, values: list[str], roles: Sequence[str]) -> str | None:
        """Resolve a SINGLE-valued axis, or refuse. Never a hardcoded tie-break."""
        if not values:
            return None
        if len(values) == 1:
            return values[0]
        ranked = [(self.precedence.get((axis, v)), v) for v in values]
        if any(r is None for r, _ in ranked):
            unranked = sorted(v for r, v in ranked if r is None)
            raise AmbiguousTrait(
                f"roles {list(roles)} conflict on the single-valued axis {axis!r}: "
                f"{sorted(values)}, and {unranked} have no declared precedence. "
                f"Rank them (higher wins) — st will not pick for you, because a "
                f"tie-break in code is the closed enum one layer up.")
        top = max(r for r, _ in ranked)
        winners = sorted(v for r, v in ranked if r == top)
        if len(winners) > 1:
            raise AmbiguousTrait(
                f"roles {list(roles)} conflict on {axis!r}: {winners} are declared "
                f"at the SAME precedence ({top}). A rank that does not order the "
                f"values it ranks cannot resolve them.")
        return winners[0]


def _as_list(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v]


def default_catalog() -> Catalog:
    """The built-in three, nothing declared. What a files-only deployment gets, and
    it must behave exactly as the enum did — that is the compatibility floor."""
    return Catalog()
