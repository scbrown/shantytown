"""tier — the orchestration tier. worker / lead / administrator.

Built to docs/roles.md. The middle role is the whole point:
A LEAD IS NOT A SMALLER ADMINISTRATOR. A LEAD IS A WORKER THAT ALSO ABSORBS.

Why it exists, measured not theoretical: one agent received every stop report
from 14 crew, and the failure wasn't overload — absorbing and delegating compete
for the same attention. A coordinator who stops to do a two-minute fix isn't
coordinating; one who never does is a router that adds latency to trivia. The
lead tier makes "just do it" a *legitimate* outcome at the layer where the
information already is.

THE FOUR OPEN QUESTIONS (roles.md) — RULED, as the design author:

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

# The three roles the built-in PROCESS is defined for — depth-2, a-lead-absorbs,
# rise-past-a-dead-lead. It is no longer the list of roles that may EXIST: a
# deployment declares its own (traits.py, GitHub #37), and what used to be an enum
# membership test is now "is this role described, and what are its traits?".
#
# Kept as a name because plenty of code legitimately means THESE THREE — the tier
# order `st start` boots in, the modes' role selectors, the capability gate. Those
# are statements about the built-in process, not about the vocabulary.
VALID_ROLES = ("worker", "lead", "administrator")

# GENERATED PANE NAMES. A card with no pane names no session, and every surface
# that launches, attaches, stops or supervises resolves an agent THROUGH its pane —
# so a pane-less card is an agent that exists and cannot be run, and the only fix
# used to be hand-editing JSON. Every card write goes through pane_for(), so a
# projected or role-set card is startable the moment it is written.
#
# The `st-` prefix, not `shanty-`: `shanty` is a different program on the same PATH
# (cli.py's docstring says why the binary is `st`), and a pane prefix is how an
# operator reads WHOSE session a tmux row is on a host running more than one
# orchestrator. It matches the launcher's own fallback (cli._session_for).
#
# An existing pane is NEVER overwritten — a card that already names one keeps it,
# whatever the convention was when it was written.
PANE_PREFIX = "st-"


def pane_for(name: str, existing: str | None = None) -> str:
    """The pane this agent's session lives in: its own if it has one, else a
    generated `st-<name>`."""
    return existing or f"{PANE_PREFIX}{name}"


def strays(sessions, known_panes, agent_names) -> list[tuple[str, str | None]]:
    """Live sessions no card claims, paired with the agent each one impersonates.

    Returns [(session, agent_or_None)], sorted. `agent` is set when the session's
    LAST hyphen-segment is a roster name — `aegis-crew-goldblum` -> `goldblum` —
    which is the shape a renaming leaves behind: same agents, same workspaces, a
    prefix nobody updated.

    Matching on the last segment rather than a substring is deliberate. A
    substring test makes `ian` match `shanty-sebastian`, and a false duplicate
    here is expensive in a specific way: the remedy for a real one is killing a
    session, so an over-eager detector points a human at a live agent's pane and
    tells them it is debris.

    A stray that maps to NO roster name is still returned, with agent=None. It is
    a weaker signal — it may be somebody's own shell on the same socket — but
    dropping it would rebuild the blind spot one size smaller, and this function
    exists because a check that only looked where it expected saw nothing wrong
    for hours (aegis-np4x1).
    """
    known, roster = set(known_panes), set(agent_names)
    out = []
    for s in sessions:
        if s in known:
            continue
        tail = s.rsplit("-", 1)[-1]
        out.append((s, tail if tail in roster else None))
    return sorted(out)


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
    return [a for a in registry.all().exact() if a.reports_to == lead]


def plan_role_set(registry: Registry, agent_name: str, role: str,
                  reports: list[str] | None = None, catalog=None) -> RolePlan:
    """Resolve what role set would do. No writes. Refuses at plan time.

    Refusing here (not at write time) means --dry-run shows the refusal too, and
    a bad hierarchy never half-lands.

    `catalog` (traits.Catalog) is what replaced the enum membership test (#37). It
    answers two questions the enum could only answer for three names: does this
    role EXIST, and is it in the tree at all? An UNATTACHED role — an advisor, an
    observer, a relay — is planned as a card and nothing else: no reports_to is
    required, none is invented, and the depth-2 rules do not apply, because they
    are rules about a tree this role is not in. That single conditional is what
    made those roles inexpressible.

    None = the built-in three, behaving exactly as the enum did.

    EVERY PLANNED CARD CARRIES ITS HARNESS. The writes are freshly built Agents,
    and files.set() re-merges launch config (harness/model/workspace) from disk,
    so leaving it off persisted fine — but the plan is READ before it is written,
    by two things that then read the wrong program: the capability gate
    (_require_writes_hostable asks harness.for_card of a card whose field it just
    dropped, so a card on a stopless harness is gated as Claude Code — aegis-85ox
    exactly, one layer up) and the settings emitter (`role set` on a codex card
    wrote Claude Code's artifact and nothing the agent reads — measured on a
    live store while wiring codex up). An in-memory card that silently claims a
    different program than the one on disk is the same failure as launching one.
    """
    from . import traits as traits_mod
    catalog = catalog if catalog is not None else traits_mod.default_catalog()
    if not catalog.describes(role):
        raise ValueError(
            f"unknown role {role!r}; declared roles: "
            f"{', '.join(catalog.known())}. Declare it as [roles.{role}] in "
            f"shantytown.toml (or in the graph) — the role list is the "
            f"deployment's, not st's.")
    agent = registry.get(agent_name)          # LookupError if unknown
    reports = reports or []

    plan = RolePlan()

    # NOT IN THE TREE AT ALL. Checked before every tier rule below, because each of
    # those rules presumes a tree position: an unattached role has no reports_to to
    # require, no lead to be under, and no escalation path to wire. Giving one
    # reports is a refusal rather than a silent drop — somebody asked for routing
    # that this role cannot carry, and quietly writing a card without it is how a
    # tier comes to be decorative.
    if catalog.of(role).unattached:
        if reports:
            raise ValueError(
                f"{role!r} is unattached (it is not in the reporting tree), so it "
                f"cannot take reports {reports}. Point them at a lead or the "
                f"administrator.")
        plan.writes.append(Agent(name=agent_name, role=role, reports_to=None,
                                 pane=pane_for(agent_name, agent.pane),
                                 harness=agent.harness))
        return plan

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
                                 reports_to=agent.reports_to,
                                 pane=pane_for(agent_name, agent.pane),
                                 harness=agent.harness))
        return plan

    if role == "administrator":
        # Q4: an administrator reports to nobody (it is the root).
        plan.writes.append(Agent(name=agent_name, role="administrator",
                                 reports_to=None, pane=pane_for(agent_name, agent.pane),
                                 harness=agent.harness))
        # reports handed to an administrator are direct (Q4: worker with no lead)
        for r in reports:
            ra = registry.get(r)
            plan.writes.append(Agent(name=r, role=ra.role, reports_to=agent_name,
                                     pane=pane_for(r, ra.pane), harness=ra.harness))
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
        admin = find_administrator(registry)
        if admin and admin != agent_name:
            lead_reports_to = admin
    plan.writes.append(Agent(name=agent_name, role="lead",
                             reports_to=lead_reports_to,
                             pane=pane_for(agent_name, agent.pane),
                             harness=agent.harness))
    for r in reports:
        ra = registry.get(r)
        plan.writes.append(Agent(name=r, role=ra.role, reports_to=agent_name,
                                 pane=pane_for(r, ra.pane), harness=ra.harness))
        plan.routes.append((r, agent_name))   # emit the stop-hook routing
    return plan


def role_set(registry: MutableRegistry, agent_name: str, role: str,
             reports: list[str] | None = None, dry_run: bool = False,
             catalog=None, root=None) -> RolePlan:
    """`root` is threaded for ONE reason: the deployment can name a card's
    program without the card saying so (`[harness]` / `[harness.by_role]`), and
    that answer lives under the root. The gate below must ask the program the
    LAUNCHER will resolve, not the one a card-only reading implies — otherwise a
    fleet whose config puts leads on a stopless program passes role set and
    refuses at `st new`, which is aegis-85ox with a config file in the middle.
    None keeps the card-only answer, which is what every caller without a root
    already got."""
    plan = plan_role_set(registry, agent_name, role, reports, catalog=catalog)
    # Capability gate (aegis-w5l9). A lead/administrator RECEIVES stop events, so
    # its harness must declare blocking stop hooks; refuse BEFORE any write, so
    # the registry never holds a tier card the fleet can never start (and its
    # settings.json is never emitted for it). This runs for dry_run too — the
    # refusal is a property of the plan, not the write, so `--dry-run` surfaces
    # it exactly as it does the hierarchy refusals. Gating here (the write path)
    # rather than only in `_cmd_role` protects every caller of role_set, not one
    # command; adapters.md documented the gate firing at role-set time and it
    # never did — the check lived only on the `st new` launch path.
    _require_writes_hostable(plan, root)
    if not dry_run:
        for a in plan.writes:
            registry.set(a)
    return plan


def _require_writes_hostable(plan: RolePlan, root=None) -> None:
    """Refuse the plan if any WRITTEN card's role needs a stop capability its
    harness lacks. Local imports keep tier free of a load-time runtime/harness
    dependency (neither imports tier, so no cycle — but the layer stays clean)."""
    from . import harness, runtime
    for card in plan.writes:
        if card.role in runtime._ROLES_NEEDING_STOP:
            runtime.require_capability(harness.for_card(card, root=root), card,
                                       consequence="Nothing written.")


# --- stop-hook routing: a worker's stop event reaches its lead. THE TIER. ---

class Reason(Enum):
    NEEDS_AUTHORITY = "needs-authority"     # exceeds the lead's access
    NEEDS_DECISION = "needs-decision"       # a human/owner must choose
    TOO_LARGE = "too-large"                 # bigger than a lead absorbs
    BLOCKED_ON_HUMAN = "blocked-on-human"
    LEAD_UNREACHABLE = "lead-unreachable"   # Q3: the lead is down, rose to admin
    UNTRACKED_WORK = "untracked-work"       # a report is ACTING with an empty hook
    # NOTE: "i was busy" is deliberately NOT here. Capacity is a capacity problem
    # and must surface as one (absorb-rate), not be laundered as an escalation.


# UNTRACKED_WORK is the one reason that does NOT describe a stop (untracked.py,
# aegis-fv2zc). It rides the stop-event channel because that channel is the only
# one that reaches a destination's MODEL — the inbox is a pure read that nothing
# polls, so an alert left there would be delivered and unread, which is the
# declared-but-inert failure this harness keeps naming. It is NOT a stop, and the
# drain must not render it as one: stop_event._compose_reason gives it its own
# section, and the drain's mid-flight DEFER must never hold it back (the sender
# being busy is the entire content of the alert).
GOVERNANCE_REASONS = (Reason.UNTRACKED_WORK.value,)


def is_governance(reason: str | None) -> bool:
    """Is this event an ALERT ABOUT an agent rather than a report that it
    stopped? One predicate, so the drain's two consumers (the defer gate and the
    renderer) cannot disagree about which events are which."""
    return reason in GOVERNANCE_REASONS


@dataclass(frozen=True)
class LeadStatus:
    """WHY a lead is or is not reachable — not just whether.

    `lead-unreachable` has two causes that want OPPOSITE actions from the
    coordinator, and collapsing them into one bool made them one string:

        the lead is DOWN            -> restart it
        the lead is UP, no `drain`  -> RELAUNCH it (its card was promoted after
                                       launch; settings are read once, the card
                                       continuously, so the process never got
                                       the direction its role now implies)

    Measured: the second fired ~6 times in one evening during a restructure and
    was absorbed as noise every time, because the operator could SEE the lead
    was up and the alert said "unreachable". An alert whose stated reason
    contradicts what the operator observes gets classified as noise — and then a
    genuinely down lead is indistinguishable from it.

    Bool-compatible ON PURPOSE (`__bool__`), so every existing `lead_is_up`
    that returns a plain bool keeps working unchanged and callers that want the
    detail ask for it. A required richer type here would have made this a
    breaking change to a routing predicate, which is not a thing to do to the
    path that carries stop events.
    """
    up: bool
    detail: str = ""

    def __bool__(self) -> bool:
        return self.up


@dataclass
class Routing:
    """Where a worker's stop event goes, and whether it rose."""
    worker: str
    to: str                      # the recipient (lead, or administrator)
    rose: bool                   # did it rise past a lead?
    reason: Reason | None = None
    # WHY the reason fired, when the probe could say. Empty when it could not,
    # never a guess — a fabricated cause on an escalation is worse than none.
    detail: str = ""

    def render(self) -> str:
        base = f"  {self.worker} stop -> {self.to}"
        if self.rose:
            base += f"  (ROSE: {self.reason.value if self.reason else '?'}"
            base += f" — {self.detail})" if self.detail else ")"
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
        admin = find_administrator(registry)
        if admin is None:
            raise LookupError(f"{worker} has no lead and there is no administrator — its stop goes nowhere")
        return Routing(worker=worker, to=admin, rose=False)

    lead = registry.get(a.reports_to)
    if lead.role != "lead" and lead.role != "administrator":
        raise LookupError(f"{worker} reports to {lead.name} which is a {lead.role}, not a lead/administrator")

    if lead.role == "administrator":
        return Routing(worker=worker, to=lead.name, rose=False)

    verdict = lead_is_up(lead.name)
    if not verdict:
        # Q3: rise to the administrator, LOUDLY — and SAY WHICH KIND of
        # unreachable. `getattr` rather than an isinstance check because a
        # plain bool is a valid verdict from any caller's own predicate and
        # must keep working; absent detail stays empty rather than invented.
        detail = getattr(verdict, "detail", "") or ""
        admin = find_administrator(registry)
        if admin is None:
            raise LookupError(f"lead {lead.name} is down and there is no administrator — {worker}'s stop is stranded")
        return Routing(worker=worker, to=admin, rose=True,
                       reason=Reason.LEAD_UNREACHABLE, detail=detail)

    return Routing(worker=worker, to=lead.name, rose=False)


def find_administrator(registry: Registry) -> str | None:
    """Who receives a rise. PUBLIC because it must have exactly ONE answer.

    `anchor` tells a worker where its stop events go; `route_stop` decides where
    they actually go. When those two computed "is there an administrator?"
    separately they were free to disagree, and a diagnostic that disagrees with
    the router it describes is worse than no diagnostic (aegis-j1dzp). One
    function, so the claim and the behaviour cannot drift apart.
    """
    for a in registry.all().exact():
        if a.role == "administrator":
            return a.name
    return None


# The old private name, kept because this module's own call sites are not the
# only thing that could reference it.
_find_administrator = find_administrator


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
