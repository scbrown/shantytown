"""tier — the orchestration tier. worker / lead / administrator.

Built to docs/roles.md (aegis-rpo1). The middle role is the whole point:
A LEAD IS NOT A SMALLER ADMINISTRATOR. A LEAD IS A WORKER THAT ALSO ABSORBS.

Why it exists, measured not theoretical: one agent received every stop report
from 14 crew, and the failure wasn't overload — absorbing and delegating compete
for the same attention. A coordinator who stops to do a two-minute fix isn't
coordinating; one who never does is a router that adds latency to trivia. The
lead tier makes "just do it" a *legitimate* outcome at the layer where the
information already is.

THE FOUR OPEN QUESTIONS (roles.md) — RULED, as the design author (aegis-rpo1):

  Q1 Can a lead have leads?            RULED: NO. Depth 2 exactly. N tiers is an
                                       org chart. set_role refuses a lead whose
                                       reports_to is itself a lead.
  Q2 Who assigns leads?               RULED: config, via `st role set`. Not
                                       dynamic. A hierarchy that reorganises
                                       itself is a scheduler, and roles.md says
                                       this is NOT a mayor.
  Q3 What when a lead is DOWN?        RULED: reports' stop events RISE to the
                                       administrator, LOUDLY, carrying the reason
                                       `lead-unreachable`. A silent fallback is
                                       how a tier stops existing unnoticed. This
                                       is the one most likely to be got wrong, so
                                       it is a named escalation reason and a test.
  Q4 Admin ever see a worker direct?  RULED: only a worker with NO lead. If it
                                       has a lead, the admin sees it only via
                                       escalation. Otherwise the filter has a
                                       hole and the tier is decorative.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum

from .protocols import Agent, Registry

VALID_ROLES = ("worker", "lead", "administrator")


# --- role set: generative. Writes the card AND the routing in one operation. ---

class MutableRegistry(Registry):
    """A registry you can write to. FilesRegistry satisfies it via set()."""
    def set(self, agent: Agent) -> None: ...


@dataclass
class RolePlan:
    """What `role set` WOULD write. --dry-run returns this and stops.

    Card and routing are one plan, so they cannot disagree — a lead card with no
    stop-hook routing is the declared-but-inert failure this harness exists to
    avoid.
    """
    writes: list[Agent] = field(default_factory=list)   # cards to write
    routes: list[tuple[str, str]] = field(default_factory=list)  # (worker, -> lead) stop routing

    def render(self) -> str:
        lines = [f"  card    {a.name}: role={a.role} reports_to={a.reports_to}" for a in self.writes]
        lines += [f"  hook    {w} stop -> {lead}" for w, lead in self.routes]
        return "\n".join(lines) or "  (no changes)"


def _reports_of(registry: Registry, lead: str) -> list[Agent]:
    return [a for a in registry.all() if a.reports_to == lead]


def plan_role_set(registry: Registry, agent_name: str, role: str,
                  reports: list[str] | None = None) -> RolePlan:
    """Resolve what role set would do. No writes. Refuses at plan time.

    Refusing here (not at write time) means --dry-run shows the refusal too, and
    a bad hierarchy never half-lands.
    """
    if role not in VALID_ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {VALID_ROLES}")
    agent = registry.get(agent_name)          # LookupError if unknown
    reports = reports or []

    plan = RolePlan()

    if role == "worker":
        # Demotion. Its former reports become orphans unless re-pointed elsewhere
        # — surface that rather than silently strand them.
        stranded = [a.name for a in _reports_of(registry, agent_name)]
        if stranded:
            raise ValueError(
                f"{agent_name} -> worker would strand its reports {stranded}. "
                f"Re-point them first (they need a lead or the administrator)."
            )
        plan.writes.append(Agent(name=agent_name, role="worker",
                                 reports_to=agent.reports_to, pane=agent.pane))
        return plan

    if role == "administrator":
        # Q4: an administrator reports to nobody (it is the root).
        plan.writes.append(Agent(name=agent_name, role="administrator",
                                 reports_to=None, pane=agent.pane))
        # reports handed to an administrator are direct (Q4: worker with no lead)
        for r in reports:
            ra = registry.get(r)
            plan.writes.append(Agent(name=r, role=ra.role, reports_to=agent_name, pane=ra.pane))
        return plan

    # role == "lead"
    # Q1: depth 2. The new lead may not itself report to a lead — checked once,
    # regardless of whether it has reports (a lead with 0 reports is still a lead
    # under a lead if its own reports_to is a lead).
    if agent.reports_to:
        up = registry.get(agent.reports_to)
        if up.role == "lead":
            raise ValueError(
                f"{agent_name} reports to lead {up.name}; a lead under a lead is depth 3 (Q1). "
                f"{up.name} must be an administrator, or {agent_name} must report elsewhere."
            )
    for r in reports:
        ra = registry.get(r)                  # LookupError if unknown report
        # Q1: a lead's report may not itself be a lead.
        if ra.role == "lead":
            raise ValueError(
                f"{r} is a lead; a lead cannot report to another lead (depth 2, roles.md Q1). "
                f"Demote {r} to worker first, or make {agent_name} an administrator."
            )
        if r == agent_name:
            raise ValueError(f"{agent_name} cannot report to itself (cycle)")

    # A lead MUST have somewhere to escalate. If it has no reports_to yet, wire it
    # to the sole administrator (the common case). If none exists, leave it None —
    # `roles --check` will flag it as an orphan lead rather than us pretending it
    # has an escalation path. Generative, but honest about the gap.
    lead_reports_to = agent.reports_to
    if lead_reports_to is None:
        admin = _find_administrator(registry)
        if admin and admin != agent_name:
            lead_reports_to = admin
    plan.writes.append(Agent(name=agent_name, role="lead",
                             reports_to=lead_reports_to, pane=agent.pane))
    for r in reports:
        ra = registry.get(r)
        plan.writes.append(Agent(name=r, role=ra.role, reports_to=agent_name, pane=ra.pane))
        plan.routes.append((r, agent_name))   # emit the stop-hook routing
    return plan


def role_set(registry: MutableRegistry, agent_name: str, role: str,
             reports: list[str] | None = None, dry_run: bool = False) -> RolePlan:
    plan = plan_role_set(registry, agent_name, role, reports)
    if not dry_run:
        for a in plan.writes:
            registry.set(a)
    return plan


# --- stop-hook routing: a worker's stop event reaches its lead. THE TIER. ---

class Reason(Enum):
    NEEDS_AUTHORITY = "needs-authority"     # exceeds the lead's access
    NEEDS_DECISION = "needs-decision"       # a human/owner must choose
    TOO_LARGE = "too-large"                 # bigger than a lead absorbs
    BLOCKED_ON_HUMAN = "blocked-on-human"
    LEAD_UNREACHABLE = "lead-unreachable"   # Q3: the lead is down, rose to admin
    # NOTE: "i was busy" is deliberately NOT here. Capacity is a capacity problem
    # and must surface as one (absorb-rate), not be laundered as an escalation.


@dataclass
class Routing:
    """Where a worker's stop event goes, and whether it rose."""
    worker: str
    to: str                      # the recipient (lead, or administrator)
    rose: bool                   # did it rise past a lead?
    reason: Reason | None = None

    def render(self) -> str:
        base = f"  {self.worker} stop -> {self.to}"
        if self.rose:
            base += f"  (ROSE: {self.reason.value if self.reason else '?'})"
        return base


def route_stop(registry: Registry, worker: str, lead_is_up=None) -> Routing:
    """Route a worker's stop event. Q3 + Q4 live here.

    lead_is_up(name) -> bool tells us whether the lead is reachable. Default:
    assume up. When a lead is DOWN, the event RISES to the administrator LOUDLY
    with reason lead-unreachable — never silently queued (Q3).
    """
    lead_is_up = lead_is_up or (lambda _n: True)
    a = registry.get(worker)

    if a.reports_to is None:
        # Q4: a worker with no lead is seen by the administrator directly.
        admin = _find_administrator(registry)
        if admin is None:
            raise LookupError(f"{worker} has no lead and there is no administrator — its stop goes nowhere")
        return Routing(worker=worker, to=admin, rose=False)

    lead = registry.get(a.reports_to)
    if lead.role != "lead" and lead.role != "administrator":
        raise LookupError(f"{worker} reports to {lead.name} which is a {lead.role}, not a lead/administrator")

    if lead.role == "administrator":
        return Routing(worker=worker, to=lead.name, rose=False)

    if not lead_is_up(lead.name):
        # Q3: rise to the administrator, LOUDLY.
        admin = _find_administrator(registry)
        if admin is None:
            raise LookupError(f"lead {lead.name} is down and there is no administrator — {worker}'s stop is stranded")
        return Routing(worker=worker, to=admin, rose=True, reason=Reason.LEAD_UNREACHABLE)

    return Routing(worker=worker, to=lead.name, rose=False)


def _find_administrator(registry: Registry) -> str | None:
    for a in registry.all():
        if a.role == "administrator":
            return a.name
    return None


# --- the lead's decision: absorb / delegate / escalate ---------------------

class Decision(Enum):
    ABSORB = "absorb"       # it's light. do it. nothing rises.
    DELEGATE = "delegate"   # hand to another worker. nothing rises.
    ESCALATE = "escalate"   # needs the administrator. rises, WITH A REASON.


@dataclass
class LeadState:
    """A lead holds AT MOST ONE absorbed task. The rule that keeps a lead a lead.

    Enforced by the harness, not by intent (roles.md). Absorbing is logged as a
    decision so 'this lead never delegates' is a query, not a vibe.
    """
    name: str
    absorbed: str | None = None                 # the one task being absorbed, if any
    log: list[tuple[str, str]] = field(default_factory=list)  # (item, decision)

    @property
    def absorb_rate(self) -> float:
        if not self.log:
            return 0.0
        return sum(1 for _, d in self.log if d == Decision.ABSORB.value) / len(self.log)


@dataclass
class Handling:
    item: str
    decision: Decision
    reason: Reason | None = None
    note: str = ""

    def render(self) -> str:
        s = f"  {self.item}: {self.decision.value}"
        if self.reason:
            s += f" ({self.reason.value})"
        if self.note:
            s += f" — {self.note}"
        return s


def handle_stop(state: LeadState, item: str, *,
                is_light: bool, escalate_reason: Reason | None = None,
                delegate_to: str | None = None) -> Handling:
    """A lead decides what to do with a report's stopped work.

    The caller (the lead agent) supplies the judgement (is_light, a reason, a
    delegate target); this function ENFORCES the tier's rules on that judgement:
      - a lead already holding an absorbed task may NOT absorb a second — it must
        delegate or escalate (the rule that keeps a lead a lead)
      - an escalation MUST carry a reason, and "busy" is not a valid one
    Every decision is logged, so absorb-rate is a query.
    """
    if escalate_reason is not None:
        h = Handling(item, Decision.ESCALATE, reason=escalate_reason)
        state.log.append((item, Decision.ESCALATE.value))
        return h

    if delegate_to is not None:
        h = Handling(item, Decision.DELEGATE, note=f"-> {delegate_to}")
        state.log.append((item, Decision.DELEGATE.value))
        return h

    if is_light:
        if state.absorbed is not None:
            # RULE: at most one absorbed task. A second must not silently queue.
            raise Capacity(
                f"lead {state.name} already absorbing {state.absorbed!r}; "
                f"cannot absorb {item!r} too. Delegate or escalate it. "
                f"(A lead that absorbs a second task is a worker, and the tier collapsed.)"
            )
        state.absorbed = item
        h = Handling(item, Decision.ABSORB)
        state.log.append((item, Decision.ABSORB.value))
        return h

    # not light, no reason, no target — the caller must decide, we won't guess.
    raise ValueError(
        f"{item} is not light and has neither a delegate target nor an escalate "
        f"reason. A lead must DECIDE — silence is not a fourth option."
    )


def release(state: LeadState, item: str) -> None:
    """The absorbed task finished; the lead can absorb again."""
    if state.absorbed == item:
        state.absorbed = None


class Capacity(Exception):
    """A lead is at capacity. This is a capacity signal, surfaced — not an error
    to swallow. If a lead hits this often, the tier isn't working; that is the
    absorb-rate telling you, loudly."""
