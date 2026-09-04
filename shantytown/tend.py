"""tend — supervision. The one command in this repo that RESTARTS things.

WHY IT IS A COMMAND AND NOT A FLAG (the count goes 13 -> 14, deliberately).
`st crew` is a READ, and this is the only surface that can create a session and
launch an agent. Hiding a respawn behind a flag on a read is how a consequence
gets lost: someone runs the safe-looking thing and a launcher fires. The verb is
its own command so the mutation is visible in the shell history.

WHY `tend` (the name was left open, so: ruled). `watch` claims observation and
this ACTS. `keep` says nothing about what it does. `tend` is looking after
something living — it observes, it acts when it must, and it does not pretend
the acting is free. Every other name we tried reads as a monitor, and a monitor
that silently restarts your fleet is the bug this module exists because of.

WHAT IT WILL NOT DO, and each one is a bug someone paid for:

  It will not respawn a RETIRED agent. A watchdog that cannot tell "died" from
  "was deliberately killed" reverted a considered shutdown of eight agents in
  about sixty seconds, silently, while a human was writing down that they were
  down. Retirement is durable (it lives on the card, not in a process) and it is
  honoured before anything else is even looked at.

  It will not act quietly. Every respawn logs. A RETIRED agent found ALIVE is an
  ESCALATION, not a line — something else is respawning what we agreed to stop.
  Silence was the defect; the restart was only the mechanism.

  It will not read a live pane as a healthy agent. `up` is not `can report`.
  Eight agents were alive and carried no stop-event wiring at all: green, and
  deaf. So every live agent is checked against the RUNNING PROCESS (the third
  leg, runtime.live_wiring) and a deaf one is REPORTED — a pass that could not
  fail is not a pass.

  It will not type into a working agent. If a session appears between the look
  and the launch, triage judges it and a busy pane is refused. The verdict is
  triage's, not a second opinion written here.

  It will not launch into a directory that does not exist. ensure_workspace runs
  first, and refuses rather than dropping an agent into nothing.

  It will not cycle a STALE agent on its own. An agent running settings older
  than the file has old hooks; killing a mid-flight agent to fix that is worse
  than the staleness. It is REPORTED as a candidate. The rule this proposes, for
  a human to accept or reject rather than for this module to assume:
      cycle a STALE agent only when it is also IDLE and holds no background
      shells — i.e. only when `st crew` would already call it free — and even
      then only on an explicit `--cycle-stale`, never on a default pass.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import triage as triage_mod
from .protocols import Agent
from .runtime import asks_a_question, live_wiring
from .workspace import WorkspaceError, ensure_workspace


# What a pass decided about one agent. `acted` is separate from `verdict` so the
# report can never imply a mutation it did not make — a dry run produces the same
# verdicts with acted=False everywhere.
OK = "ok"                     # up, wired, nothing to do
RESPAWNED = "respawned"       # it was down; it is not any more
WOULD = "would-respawn"       # --dry-run: down, and we stopped there
RETIRED = "retired"           # deliberately retired. NOT a fault, NOT respawned
SURVIVOR = "survivor"         # retired after this still-live session was born
STOPPED = "stopped"           # down because somebody ran `st stop` (aegis-k9068).
                              # NOT a fault, NOT respawned, and NOT a retirement:
                              # `st new <agent>` brings it straight back.
                              # It exists because `st stop` DELETES the launch
                              # stamp itself (cli `_launches().forget()`), so the
                              # aegis-2j2r ownership gate below catches st's own
                              # stopped agents and used to explain them with the
                              # foreign-orchestrator wording — "never launched by
                              # st ... another orchestrator owns it" — which is
                              # false in every clause for this population and
                              # sends the reader hunting an orchestrator that
                              # does not exist. Two causes, one message; this is
                              # the second one, told apart by the stop record.
                              # Refusing is still CORRECT (a deliberate stop must
                              # stay down until asked back, which is what `st
                              # stop` now promises) — only the reason was wrong.
RESURRECTED = "RESURRECTED"   # retired AND alive — something else respawned it
DEAF = "deaf"                 # alive, but the running process cannot report
STALE = "stale-settings"      # alive, running settings older than the file
BUSY = "busy"                 # a session appeared and is mid-flight — hands off
REFUSED = "refused"           # could not act, and said why (workspace, launch)
UNTENDABLE = "no-pane"        # no pane on the card: nothing to supervise
UNEQUIPPED = "unequipped"     # alive, but its workspace lacks the tool kit
BACKOFF = "backoff"           # died again too soon — waiting before the next try
CRASH_LOOP = "CRASH-LOOP"     # died repeatedly; RETIRED rather than thrashed
AUTH_DEAD = "auth-dead"       # alive, login expired: every API call fails
                              # (aegis-arma). NOT auto-relaunched on a default
                              # pass — see the rule in _live — `st tend --reauth`
                              # is the explicit one-command recovery.
BELOW_TARGET = "at-target"    # down, and NOT respawned because `--target N` is
                              # already satisfied. A cap, not a fault: the
                              # operator asked for N live agents and there are N.
GOVERNED = "governed"         # down, and NOT respawned because the USAGE
CODEX_DAEMON_WEDGED = "codex-daemon-wedged"  # named launch-depth blocker
                              # GOVERNOR's current tier excludes it (aegis-hdqej).
                              # A cap in exactly the same sense as BELOW_TARGET,
                              # and reported the same way — it comes back on its
                              # own when usage falls, so it is not a fault and
                              # must not exit non-zero. It is the ONLY thing the
                              # governor gets to do to a live fleet from in here:
                              # tend still never kills, so spinning an agent DOWN
                              # is the drain protocol asking it to stop itself
                              # (governor.Drainer), never this module reaping it.


_FAULTS = frozenset({RESURRECTED, DEAF, REFUSED, UNEQUIPPED, AUTH_DEAD, CRASH_LOOP})

# RESPAWN BACKOFF (GitHub #12). A crash-looping agent — bad card, broken
# workspace_source, poisoned settings — turns the supervisor into a respawn
# thrasher: died -> respawn -> died, forever, at whatever cadence tend runs. The
# fix has to be self-limiting in BOTH directions:
#
#   backoff   each successive death within the window waits longer, so a flapping
#             agent costs one launch per interval instead of one per pass.
#   give up   after RETRIES consecutive deaths it is RETIRED — durably, on the
#             card, the same mechanism a human uses — and reported as a FAULT.
#             A supervisor that never gives up is a supervisor that hides a
#             broken agent behind infinite optimism.
#
# The counter resets when an agent is seen ALIVE and healthy, so an agent that
# recovers is not punished for an old episode.
BACKOFF_BASE_S = 60           # 1st retry waits 1m, then 2m, 4m, 8m...
BACKOFF_RETRIES = 5           # then retire rather than thrash

# Who comes up FIRST when `--target N` cannot bring up everyone. A fleet brought up
# bottom-first is a set of workers whose stop events reach nobody, so the tier is
# filled from the root down. Unknown roles (a deployment's own, traits.py) sort
# last: they are not in the built-in reporting process, so nothing in it depends on
# them being up.
#
# THIS IS A TREE ORDER, AND IT IS NOT THE THROTTLE'S ORDER — the distinction was
# ruled on the crew-traits design bead (2026-08-01) after an interim draft deleted
# this map and derived bring-up from the new `survival` axis instead. That would
# have been wrong twice over:
#
#   * survival is DECLARED, never derived, and unset is `normal` — so on today's
#     fleet (19 cards, none declaring a band) every agent would rank identically
#     and bring-up would collapse to ALPHABETICAL. "sattler comes up first because
#     his name sorts early" is exactly the arbitrary ordering the trait model
#     exists to replace, arriving as a silent regression under a change described
#     as preserving behaviour.
#   * the two answer different questions. This one is about the REPORTING TREE
#     (boot a lead before its reports, or their stop events rise with
#     `lead-unreachable`). Survival is about who is SHED when usage climbs. An
#     agent can be structurally central and cheap to lose, or peripheral and the
#     one thing that must stay up.
#
# So they stay separate, and the throttle reads traits.survival_key instead. The
# sign trap that motivated the merge is handled where it belongs — the bands are
# NAMES (`first`…`last`), so there is no direction left to remember.
_TIER_ORDER = {"administrator": 0, "lead": 1, "worker": 2}


def _tree_depth(agent, fleet, _max_hops: int = 64) -> int:
    """How far this agent sits from the root of the REPORTING TREE (aegis-6snzw).

    Bring-up order exists for one reason, stated at the top of this module: boot a
    lead before its reports, or their stop events rise with lead-unreachable. That
    is a statement about the TREE, and the sort keyed on ROLE NAME — a proxy that
    does not track it.

    dearing measured the gap on this fleet (aegis-j5uek): 7 of 13 live agents are
    workers reporting straight to the root, so they sit at depth 1 alongside the
    leads. The invariant held anyway, but BY COINCIDENCE — no two agents shared a
    role while one reported to the other. THE FAILURE NEEDS ONLY A LEAD REPORTING
    TO A LEAD: both land in the same `_TIER_ORDER` tier, are then ordered
    alphabetically, and the subordinate can boot before its supervisor — exactly
    what the ordering exists to prevent. Nothing forbids that shape, and the trait
    model exists so a deployment can add role shapes st never anticipated.

    Depth makes it hold BY CONSTRUCTION: a supervisor's depth is strictly less
    than its report's, so the sort cannot invert them whatever their roles are.

    CYCLE-SAFE, deliberately. A malformed `reports_to` cycle (a->b->a) is a config
    error, not something bring-up should hang or crash on — tend runs unattended
    on a timer. The hop cap returns a large depth, which sorts the cycle LAST:
    agents whose position cannot be established do not get to go first.

    An agent naming a `reports_to` that is not in the fleet is treated as reporting
    to the root. That is the same "unknown sorts as shallow-but-known" answer the
    old `_TIER_ORDER.get(role, len(...))` gave for unknown ROLES, and it keeps a
    partial roster from reordering everyone else.
    """
    by_name = {a.name: a for a in fleet}
    depth, seen, cur = 0, {agent.name}, agent
    for _ in range(_max_hops):
        parent = getattr(cur, "reports_to", None)
        if not parent:
            return depth                      # reports to the root
        if parent in seen:
            return _max_hops                  # cycle: position unknowable, sort last
        if parent not in by_name:
            return depth                      # off-roster parent: treat as root
        seen.add(parent)
        cur = by_name[parent]
        depth += 1
    return _max_hops                          # deeper than any real tree


@dataclass(frozen=True)
class Finding:
    agent: str
    state: str            # up | down | no pane
    verdict: str
    why: str = ""
    acted: bool = False   # did this pass MUTATE anything for this agent?


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    started: float = 0.0
    dry_run: bool = False

    @property
    def acted(self) -> list[Finding]:
        return [f for f in self.findings if f.acted]

    @property
    def faults(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict in _FAULTS]

    def healthy(self) -> bool:
        """No faults. A pass that RESPAWNED something is still healthy — it did
        its job. A pass that found a resurrected retiree, a deaf agent or a
        refusal is not, and the exit code says so."""
        return not self.faults

    def render(self) -> str:
        lines = []
        for f in self.findings:
            mark = "!" if f.verdict in _FAULTS else ("+" if f.acted else " ")
            lines.append(f"  {mark} {f.agent:<12} {f.state:<8} {f.verdict:<14} {f.why}")
        n_act = len(self.acted)
        head = "would act on" if self.dry_run else "acted on"
        lines.append("")
        lines.append(f"  {len(self.findings)} agent(s) · {head} {n_act} · "
                     f"{len(self.faults)} fault(s)")
        return "\n".join(lines)

    def as_record(self) -> dict:
        """The health signal (a watchdog with no watchdog is a silent single
        point of recovery failure). Written after every pass, so the ABSENCE of a
        recent pass is detectable from outside — which is the only way anyone
        finds out the supervisor itself stopped."""
        return {
            "at": self.started,
            "dry_run": self.dry_run,
            "agents": len(self.findings),
            "acted": [f.agent for f in self.acted],
            "faults": [{"agent": f.agent, "verdict": f.verdict, "why": f.why}
                       for f in self.faults],
        }


def is_retired(card: Agent) -> bool:
    """Retirement lives on the CARD. Durable by construction: it survives a
    reboot, a `systemctl restart`, and the supervisor process dying, because it
    is not held in any of them. A retirement kept in a runtime's memory is a
    retirement that ends the next time the runtime does — which is exactly when a
    watchdog wakes up and undoes it."""
    return bool(card.retired)


def retirement_provenance(card: Agent) -> str:
    """" (retired by X at T)" — or "" when the card does not say.

    Carried into the verdicts because RESURRECTED is a forensic finding, and
    the two questions it immediately raises are "who agreed to stop this" and
    "was that before or after the thing that is now running started". Both are
    on the card; printing them turns a page into a lead. Blank when unrecorded:
    a verdict that padded itself with "retired by unknown" would make an old
    card look like a fresh mystery.
    """
    who, when = card.retired_by, card.retired_at
    if not who and not when:
        return ""
    return f" (retired by {who or 'unrecorded'} at {when or 'unrecorded time'})"


def _by_at(stop) -> str:
    """" by X at T" — or "" when the stop record does not say (aegis-k9068).

    Same discipline as retirement_provenance above and blank for the same reason:
    a line that padded itself with "by unknown" would make a plainly-recorded stop
    look like a mystery. `at` is a float epoch; render it as the local timestamp a
    reader can compare against `st crew`.
    """
    who = getattr(stop, "by", "") or ""
    when = getattr(stop, "at", None)
    stamp = ""
    if when:
        try:
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(when)))
        except (TypeError, ValueError, OSError):
            stamp = ""
    if not who and not stamp:
        return ""
    return f" by {who or 'unrecorded'} at {stamp or 'unrecorded time'}"


class Tender:
    """One supervision pass. Every dependency is injected — the point of this
    class is that a test can run a whole pass with no tmux, no git, no systemd
    and no launcher, and still exercise the branch that RESPAWNS.
    """

    def __init__(self, panes, runtime, launches, *, spawn=None, refresh=None,
                 refresh_trees=None,
                 ensure=ensure_workspace, log=None, gaps=None, crashes=None,
                 retire=None, now=None, target=None, target_src=None,
                 governed=None,
                 catalog=None, stops=None, codex_block=None):
        self._panes = panes
        self._runtime = runtime
        self._launches = launches
        # codex_block(card) -> blocker | None. Injected for the same reason
        # `spawn` is, and it was the ONE dependency here that was not — it read
        # /run/user/<uid>/shantytown/codex/<name>/ off the live host from inside
        # a staticmethod, so a fully-constructed test (NullPanes, fake crashes,
        # injected clock) still consulted the real filesystem. Its fixtures use
        # the literal name "kelly", and a real agent called kelly with a stale
        # startup lock turned six hermetic backoff tests red on every developer
        # box while CI — which has no such path — stayed green. A suite that is
        # green in CI and red on every desk teaches people to ignore a local red
        # (aegis-9zhk2q).
        self._codex_block_fn = codex_block
        # spawn(card, session) -> None. The launcher. Injected because a test
        # that cannot spawn cannot test the only branch that matters.
        self._spawn = spawn
        # refresh(path) -> str | None. ff-only pull; returns an error string.
        self._refresh = refresh
        # refresh_trees(card) -> list[str]. The agent's PROJECT WORKTREES, which
        # `refresh` above does not cover: it is handed the workspace clone path,
        # so nothing in a pass ever touched a worktree. That is why 12 of 12
        # worktrees sat behind — every refresh path was event-driven (provision,
        # dispatch), and an agent that is neither provisioned nor dispatched
        # simply drifts (aegis-ib65p decision 7).
        #
        # Takes the CARD, not a path, because an agent has N worktrees across N
        # shared repos and only the card identifies which are its.
        self._refresh_trees = refresh_trees
        self._ensure = ensure
        # gaps(card) -> list of missing kit names. Injected so a pass can report
        # a half-equipped agent — nothing in the tier reported that difference,
        # which is how five agents worked a night without their tools.
        self._gaps = gaps
        # crashes: a {name: (consecutive_deaths, last_death_ts)} store. Injected so
        # a test drives the backoff without a clock or a filesystem; None disables
        # the backoff entirely (every existing caller keeps its old behaviour).
        self._crashes = crashes
        self._retire = retire
        self._now = now or time.time
        self._log = log or (lambda msg: None)
        # target: how many agents this fleet should have LIVE. None = every
        # non-retired card, which is what a pass has always meant.
        self._target = target
        # WHERE the target came from, for the held message only (aegis-tzpo1).
        # "--target 6 is already met" sent an operator hunting for a flag they
        # never passed, when the 6 was the governor's `max_agents`. A report that
        # names the wrong SOURCE is the aegis-yc864 shape one layer down: the
        # number is right and the explanation is not.
        self._target_src = target_src
        # governed(card) -> "" | why this card must not come up. The usage
        # governor's tier, injected exactly like every other policy here so a
        # test drives 45/55/75/85/97 through a whole pass with no Prometheus.
        # None = ungoverned, which is what every existing caller gets.
        self._governed = governed
        # The role catalog, used ONLY to resolve an agent's survival band for the
        # governor's exclusion check. Bring-up order is the tree order above and
        # deliberately does not read it.
        self._catalog = catalog
        # The deliberate-stop record (aegis-k9068). READ-ONLY here and consulted
        # at exactly one place: to tell st's OWN stopped agents apart from another
        # orchestrator's cards, which the launch-stamp gate alone cannot do. It
        # changes no verdict's ACTION — an unstamped agent is still not respawned
        # either way — only which of two true sentences gets printed. None keeps
        # the pre-k9068 behaviour, so every existing caller is unaffected.
        self._stops = stops

    def pass_over(self, agents: list[Agent], *, dry_run: bool = False) -> Report:
        rep = Report(started=time.time(), dry_run=dry_run)
        allowed = self._respawn_budget(agents)
        for card in sorted(agents, key=lambda a: a.name):
            rep.findings.append(self._one(card, agents, dry_run, allowed))
        return rep

    def _respawn_budget(self, agents: list[Agent]) -> set[str] | None:
        """WHICH down agents this pass may bring up, under `--target N`.

        None = no cap (every non-retired down card, the original behaviour).

        SCALE-UP-ON-LOSS, AND NOTHING ELSE. It respawns toward a count; it never
        stops a surplus. Deciding WHICH agents a fleet should consist of is
        judgment, and judgment belongs to the admin — `st` is the mechanism (the
        st-redesign epic's load-bearing split). A `tend` that could also kill would
        be a scheduler with a supervisor's permissions.

        The choice of WHO comes up is deterministic and made here, not per agent,
        because per-agent decisions cannot count: two passes over the same fleet
        must bring up the same agents, and a report in name order must not depend on
        which name happened to be reached while budget remained. Tier order, then
        name — an administrator before a lead before a worker, because a fleet
        brought up bottom-first has workers whose stop events reach nobody.
        """
        if self._target is None:
            return None
        # A GOVERNED-OUT agent never occupies a slot. Leaving it in `down` would
        # let it be picked into `allowed`, then refused one line later in _one —
        # so the fleet would sit BELOW its target with the slot held by an agent
        # the governor will not let come up, and `--target` would silently mean
        # something different whenever a tier is engaged.
        tendable = [a for a in agents
                    if a.pane and not is_retired(a) and not self._withheld(a)
                    and not self._codex_block(a)]
        live = [a for a in tendable if self._panes.exists(a.pane)]
        room = self._target - len(live)
        if room <= 0:
            return set()
        down = [a for a in tendable if not self._panes.exists(a.pane)]
        down.sort(key=lambda a: (_tree_depth(a, tendable), a.name))
        return {a.name for a in down[:room]}

    # --- one agent -----------------------------------------------------------

    def _codex_block(self, card: Agent):
        """A proven per-card daemon blocker, or None. Inspection is read-only.

        Defaults to the live probe, so production behaviour is unchanged; a test
        passes `codex_block=lambda card: None` to keep the host out of it.
        """
        if self._codex_block_fn is not None:
            try:
                return self._codex_block_fn(card)
            except Exception:  # noqa: BLE001 — same cannot-prove rule as below
                return None
        try:
            from . import codex_daemon
            found = codex_daemon.inspect(card.name)
            return found if found.blocked else None
        except Exception:  # noqa: BLE001 — an unreadable /proc is cannot-prove
            return None

    def _withheld(self, card: Agent) -> str:
        """"" if the governor allows this card up, else WHY not.

        FAIL-OPEN on any error, and that direction is the fail-safe the governor
        states by name: a policy that CANNOT be evaluated must never be the reason
        a fleet stays down. A broken reader, an unreadable catalog, a raising
        callable — each one means we do not know, and not-knowing lets the agent
        run (loudly elsewhere), it does not spin the crew down.
        """
        if self._governed is None:
            return ""
        try:
            return self._governed(card) or ""
        except Exception:                    # noqa: BLE001 — never fatal to a pass
            return ""

    def _one(self, card: Agent, agents: list[Agent], dry_run: bool,
             allowed: set[str] | None = None) -> Finding:
        if not card.pane:
            return Finding(card.name, "no pane", UNTENDABLE,
                           "no pane on the card — nothing to supervise")

        up = self._panes.exists(card.pane)

        # RETIREMENT FIRST, before anything can decide to act. Ordering is the
        # guarantee: a check that runs after the respawn logic is a check that
        # can be reached too late.
        if is_retired(card):
            prov = retirement_provenance(card)
            if up:
                # TWO WORLDS used to share one accusation. If the session was
                # born BEFORE the retirement, nothing resurrected it: retirement
                # does not kill a running pane, so this is a benign survivor.
                # Missing/unreadable timestamps stay on the loud path; a survivor
                # claim without the ordering evidence would be flattery.
                born = None
                try:
                    get_created = getattr(self._panes, "session_created")
                    born = get_created(card.pane)
                    from datetime import datetime
                    retired = datetime.fromisoformat(
                        (card.retired_at or "").replace("Z", "+00:00")
                    ).timestamp()
                except (AttributeError, TypeError, ValueError):
                    retired = None
                if born is not None and retired is not None and born < retired:
                    why = (f"session predates retirement — SURVIVED the decision "
                           f"without a respawn (session_created={born:.0f}, "
                           f"retired_at={card.retired_at}).{prov}")
                    self._log(f"SURVIVOR {card.name}: {why}")
                    return Finding(card.name, "up", SURVIVOR, why)
                # The alarm. It was born at/after retirement, or we cannot prove
                # otherwise. Something started it after the stop decision.
                why = (f"marked RETIRED and yet ALIVE in {card.pane!r} — this "
                       f"supervisor did not start it. Something else is "
                       f"respawning agents we agreed to stop. Find it before "
                       f"trusting any shutdown.{prov}")
                self._log(f"ESCALATE {card.name}: {why}")
                return Finding(card.name, "up", RESURRECTED, why)
            return Finding(card.name, "down", RETIRED,
                           f"deliberately retired — NOT a fault, NOT "
                           f"respawned{prov}")

        if blocked := self._codex_block(card):
            return Finding(card.name, "up" if up else "down", CODEX_DAEMON_WEDGED,
                           f"{CODEX_DAEMON_WEDGED}: {blocked.reason()} — "
                           f"`st new {card.name}` repairs it before launch")

        if up:
            if self._crashes is not None:
                self._crashes.clear(card.name)   # alive -> the episode is over
            # A LIVE agent the governor excludes is NOT reported here and NOT
            # touched here. tend does not kill (see GOVERNED's note), so the only
            # honest thing it can do about a running excluded agent is the normal
            # health check — its spin-down is the drain protocol, which asks, and
            # which reports separately and by name. Short-circuiting to GOVERNED
            # would ALSO drop this agent's auth-dead/deaf/unequipped checks on the
            # floor for as long as the tier holds, which is exactly when a silent
            # broken agent costs the most.
            return self._live(card, agents)
        # THE GOVERNOR, checked before the target cap so a withheld agent never
        # occupies a slot the operator asked to be filled. A cap, not a fault:
        # it comes back on its own when usage falls.
        if why := self._withheld(card):
            return Finding(card.name, "down", GOVERNED,
                           f"held: {why} (not a fault — it comes back when usage "
                           f"falls below the tier)")
        # THE TARGET CAP, checked after retirement and after liveness, and before
        # every reason to act. Held back rather than respawned — and reported as a
        # cap with the number in it, because a down agent that nothing mentions is
        # indistinguishable from one the supervisor failed to notice.
        if allowed is not None and card.name not in allowed:
            return Finding(card.name, "down", BELOW_TARGET,
                           f"held: {self._target_src or '--target'} "
                           f"{self._target} is already met "
                           f"(not a fault — raise the target to bring it up)")
        return self._respawn(card, dry_run)

    def _live(self, card: Agent, agents: list[Agent]) -> Finding:
        """An agent that EXISTS. The question is never "is the pane there" — it
        is "can this agent still report", and those are different facts."""
        # AUTH-DEAD FIRST (aegis-arma): login expired, so every API call this
        # agent makes fails — the wiring and kit checks below are moot for a
        # session nothing can run in. REPORTED, not auto-relaunched, same rule as
        # STALE and for a sharper reason: the fix (a relaunch re-reading the
        # shared credential) only works AFTER the operator re-logs in, and a
        # default pass cannot know that happened. A supervisor that relaunches
        # on every pass while the credential is still stale kill-loops the whole
        # fleet, burning each agent's frozen context for nothing. The explicit
        # command is `st tend --reauth` — one command, operator-timed.
        from .runtime import auth_expired
        plain = triage_mod.strip_attrs(self._panes.capture(card.pane, attrs=True))
        if auth_expired(self._runtime, plain):
            return Finding(card.name, "up", AUTH_DEAD,
                           "alive and LOGIN-EXPIRED: every API call fails; the "
                           "pane renders idle and nothing can run. Not respawned "
                           "by this pass — re-login on the operator session "
                           "FIRST (refreshing the shared credential), then "
                           "`st tend --reauth` relaunches every auth-dead agent "
                           "in one command")
        wiring = live_wiring(card.pane, self._panes.cmdline)
        if wiring is None:
            return Finding(card.name, "up", DEAF,
                           "could not read the running process — CANNOT TELL "
                           "whether it can report (not a pass)")
        from .roles import required_stop_directions
        missing = required_stop_directions(card, agents) - wiring.directions
        if missing:
            whence = (f" (its --settings is {wiring.settings_path})"
                      if wiring.settings_path
                      else " and its launch line carries NO --settings at all")
            return Finding(card.name, "up", DEAF,
                           f"alive but carries {sorted(wiring.directions)}, "
                           f"needs {sorted(missing)} more{whence} — green and "
                           f"dead: it cannot report and nothing will rise")
        if self._gaps is not None:
            missing = self._gaps(card)
            if missing:
                return Finding(card.name, "up", UNEQUIPPED,
                               f"alive, and its workspace is MISSING {', '.join(missing)} "
                               f"— it accepts dispatch and silently lacks the tools "
                               f"the work assumes. Re-provision, then relaunch: the "
                               f"kit is read at launch, so a file written now does "
                               f"not reach the running process")
        if self._launches is not None and self._launches.verdict(card.name) == "STALE":
            return Finding(card.name, "up", STALE,
                           "running settings OLDER than the file on disk — a "
                           "CANDIDATE for a cycle, not a reason to kill a "
                           "working agent (see the rule in tend.__doc__)")
        return Finding(card.name, "up", OK)

    def _respawn(self, card: Agent, dry_run: bool) -> Finding:
        """It is down and it was not retired. Bring it back — loudly."""
        # OWNERSHIP GATE (aegis-2j2r). st tend was one of the dark-crew trap's
        # own respawners: pilot-era registry cards for another orchestrator's
        # fleet went "down" whenever that orchestrator cycled them, and this
        # respawn brought them back primed with THIS deployment's worker
        # settings — manufacturing the very panes that carry st wiring but
        # route nothing to st (observed live: "RESPAWNED dearing ... into
        # 'aegis-crew-dearing'"). Same signal as the feed gate: an agent with
        # no launch stamp was never launched by st and is not st's to respawn.
        # CANNOT-TELL honored: if NO agent has a stamp the store proves
        # nothing, so no gate (a fresh deployment must still self-heal).
        # Fail-open: any error reading the store means NO gate (the respawn
        # proceeds as it always did) — the gate is a refinement, and a broken
        # gate must not turn the self-heal off.
        try:
            unstamped = (self._launches is not None
                         and self._launches.get(card.name) is None
                         and any(self._launches.root.glob("*.json")))
        except Exception:  # noqa: BLE001 — stubbed/legacy stores lack the API
            unstamped = False
        if unstamped:
            # WHICH KIND of unstamped? (aegis-k9068.) The gate above proves only
            # that the stamp is GONE — it cannot say why, and the two reasons want
            # opposite words and opposite actions. `st stop` forgets the stamp
            # itself, so ask the stop record before speaking for it. No record =
            # the aegis-2j2r case, wording unchanged.
            stop = None
            try:
                stop = self._stops.get(card.name) if self._stops is not None else None
            except Exception:  # noqa: BLE001 — an unreadable store is not an intent
                stop = None
            if stop is not None:
                why = (f"deliberately stopped{_by_at(stop)} — st's own agent, and "
                       f"`st stop` removed its launch stamp, so tend will not "
                       f"bring it back. `st new {card.name}` restores it.")
                self._log(f"STOPPED {card.name}: {why}")
                return Finding(card.name, "down", STOPPED, why)
            # (Citation lives here, not in the emittable string: aegis-2j2r.)
            why = ("no launch stamp — never launched by st, so not st's "
                   "to respawn (another orchestrator owns it)")
            self._log(f"REFUSED {card.name}: {why}")
            return Finding(card.name, "down", REFUSED, why)
        # BACKOFF / GIVE-UP, before anything is ensured or launched.
        if self._crashes is not None and not dry_run:
            deaths, last = self._crashes.get(card.name)
            if deaths >= BACKOFF_RETRIES:
                why = (f"died {deaths} times in a row without staying up — "
                       f"RETIRED rather than respawned again. Something about this "
                       f"agent is broken (card, workspace_source, settings), and a "
                       f"supervisor that keeps relaunching it hides that. "
                       f"`st tend --unretire {card.name}` when it is fixed.")
                self._log(f"CRASH-LOOP {card.name}: {why}")
                if self._retire is not None:
                    self._retire(card.name)
                return Finding(card.name, "down", CRASH_LOOP, why)
            wait = BACKOFF_BASE_S * (2 ** max(0, deaths - 1)) if deaths else 0
            waited = self._now() - last if last else None
            if wait and waited is not None and waited < wait:
                why = (f"died {deaths} time(s) in a row; waiting "
                       f"{wait - waited:.0f}s more before retry {deaths + 1} of "
                       f"{BACKOFF_RETRIES}")
                self._log(f"BACKOFF {card.name}: {why}")
                return Finding(card.name, "down", BACKOFF, why)

        if dry_run:
            return Finding(card.name, "down", WOULD,
                           f"would ensure {card.workspace or 'default cwd'}, "
                           f"refresh it, and launch into a new {card.pane!r}")

        # WORKSPACE FIRST. A respawn that skips this launches an agent into a
        # directory that may not exist, and the break surfaces as shell noise
        # inside a session that already came up.
        try:
            path = self._ensure(card)
        except WorkspaceError as e:
            self._log(f"REFUSED {card.name}: {e}")
            return Finding(card.name, "down", REFUSED, str(e))

        # REFRESH AT THE SAFE MOMENT. The agent is down, nothing holds the
        # checkout, and no live session can be racing it. A failure here is LOUD
        # but never blocking: refusing to start an agent over a network blip
        # trades a stale directive for an outage, which is the worse trade.
        if path and self._refresh is not None:
            err = self._refresh(path)
            if err:
                self._log(f"WARN {card.name}: clone refresh failed: {err} "
                          f"(launching anyway — a stale checkout beats no agent)")

        # ...AND ITS PROJECT WORKTREES, in the same window and for the same
        # reason (aegis-ib65p decision 7). This is the ONLY safe seam for it: the
        # agent is provably DOWN here, which is exactly the condition decision
        # 5's never-auto-pull-under-a-live-agent rule requires. Everywhere else a
        # worktree refresh could race an agent mid-edit, so everywhere else the
        # guard only ADVISES.
        #
        # Without this, every refresh path was event-driven — an agent that is
        # neither provisioned nor dispatched drifts forever, which is why the
        # fleet sweep found 12 of 12 worktrees behind, one by 155 commits.
        #
        # NEVER BLOCKING, same trade as the clone refresh above: a worktree we
        # could not bring current is reported and the agent starts anyway. The
        # underlying refresh is ff/rebase-only on a CLEAN tree and leaves a dirty
        # or conflicted one untouched, so this cannot eat uncommitted work.
        if self._refresh_trees is not None:
            try:
                for warn in self._refresh_trees(card) or []:
                    self._log(f"WARN {card.name}: worktree — {warn}")
            except Exception as e:                       # never fatal to a respawn
                self._log(f"WARN {card.name}: worktree refresh errored ({e!r})")

        # A session may have appeared between the look and here. Never type into
        # a working agent: triage owns that verdict, and we do not write a second.
        if self._panes.exists(card.pane):
            screen = self._panes.capture(card.pane)
            work = triage_mod.work_state(
                screen, self._runtime.shows_ready_ui(screen),
                # An agent stalled on a picker is emphatically not a free pane to
                # launch into: hands off for the same reason BUSY is (aegis-qxc2).
                awaiting=asks_a_question(self._runtime, screen))
            why = (f"a session appeared at {card.pane!r} while this pass ran "
                   f"(triage: {work}) — hands off")
            self._log(f"SKIP {card.name}: {why}")
            return Finding(card.name, "up", BUSY, why)

        try:
            self._panes.new_session(card.pane, cwd=card.workspace)
            if self._spawn is not None:
                self._spawn(card, card.pane)
        except Exception as e:
            self._log(f"REFUSED {card.name}: launch failed: {e}")
            return Finding(card.name, "down", REFUSED, f"launch failed: {e}")

        if self._crashes is not None:
            self._crashes.died(card.name, self._now())
        why = f"was DOWN — respawned into {card.pane!r}"
        # LOUD. The whole reason this module exists is that the last thing to do
        # this did it silently.
        self._log(f"RESPAWNED {card.name}: {why}")
        return Finding(card.name, "down", RESPAWNED, why, acted=True)
