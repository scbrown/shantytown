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
RETIRED = "retired"           # deliberately stopped. NOT a fault, NOT respawned
RESURRECTED = "RESURRECTED"   # retired AND alive — something else respawned it
DEAF = "deaf"                 # alive, but the running process cannot report
STALE = "stale-settings"      # alive, running settings older than the file
BUSY = "busy"                 # a session appeared and is mid-flight — hands off
REFUSED = "refused"           # could not act, and said why (workspace, launch)
UNTENDABLE = "no-pane"        # no pane on the card: nothing to supervise
UNEQUIPPED = "unequipped"     # alive, but its workspace lacks the tool kit


_FAULTS = frozenset({RESURRECTED, DEAF, REFUSED, UNEQUIPPED})


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


class Tender:
    """One supervision pass. Every dependency is injected — the point of this
    class is that a test can run a whole pass with no tmux, no git, no systemd
    and no launcher, and still exercise the branch that RESPAWNS.
    """

    def __init__(self, panes, runtime, launches, *, spawn=None, refresh=None,
                 ensure=ensure_workspace, log=None, gaps=None):
        self._panes = panes
        self._runtime = runtime
        self._launches = launches
        # spawn(card, session) -> None. The launcher. Injected because a test
        # that cannot spawn cannot test the only branch that matters.
        self._spawn = spawn
        # refresh(path) -> str | None. ff-only pull; returns an error string.
        self._refresh = refresh
        self._ensure = ensure
        # gaps(card) -> list of missing kit names. Injected so a pass can report
        # a half-equipped agent — nothing in the tier reported that difference,
        # which is how five agents worked a night without their tools.
        self._gaps = gaps
        self._log = log or (lambda msg: None)

    def pass_over(self, agents: list[Agent], *, dry_run: bool = False) -> Report:
        rep = Report(started=time.time(), dry_run=dry_run)
        for card in sorted(agents, key=lambda a: a.name):
            rep.findings.append(self._one(card, agents, dry_run))
        return rep

    # --- one agent -----------------------------------------------------------

    def _one(self, card: Agent, agents: list[Agent], dry_run: bool) -> Finding:
        if not card.pane:
            return Finding(card.name, "no pane", UNTENDABLE,
                           "no pane on the card — nothing to supervise")

        up = self._panes.exists(card.pane)

        # RETIREMENT FIRST, before anything can decide to act. Ordering is the
        # guarantee: a check that runs after the respawn logic is a check that
        # can be reached too late.
        if is_retired(card):
            if up:
                # The alarm. We did not do this, and something did.
                why = (f"marked RETIRED and yet ALIVE in {card.pane!r} — this "
                       f"supervisor did not start it. Something else is "
                       f"respawning agents we agreed to stop. Find it before "
                       f"trusting any shutdown.")
                self._log(f"ESCALATE {card.name}: {why}")
                return Finding(card.name, "up", RESURRECTED, why)
            return Finding(card.name, "down", RETIRED,
                           "deliberately retired — NOT a fault, NOT respawned")

        if up:
            return self._live(card, agents)
        return self._respawn(card, dry_run)

    def _live(self, card: Agent, agents: list[Agent]) -> Finding:
        """An agent that EXISTS. The question is never "is the pane there" — it
        is "can this agent still report", and those are different facts."""
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
            self._panes.new_session(card.pane)
            if self._spawn is not None:
                self._spawn(card, card.pane)
        except Exception as e:
            self._log(f"REFUSED {card.name}: launch failed: {e}")
            return Finding(card.name, "down", REFUSED, f"launch failed: {e}")

        why = f"was DOWN — respawned into {card.pane!r}"
        # LOUD. The whole reason this module exists is that the last thing to do
        # this did it silently.
        self._log(f"RESPAWNED {card.name}: {why}")
        return Finding(card.name, "down", RESPAWNED, why, acted=True)
