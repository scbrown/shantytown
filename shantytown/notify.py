"""notify — PUSH a blocked worker to the coordinator, without waiting for the
coordinator to stop.

The gap this closes (aegis-w0kk). The tier can already CLASSIFY a blocked worker —
triage.work_state returns `waiting` when a picker is up and blocking (aegis-qxc2),
and the drain spells it out. But DELIVERY of that verdict runs in the
ADMINISTRATOR's Stop hook, so the coordinator only learns a worker is blocked on
the coordinator's OWN next turn boundary. For a heads-down admin that is many
minutes; for one in a long task, effectively never. Measured: kelly sat `waiting`
unseen, and weaver parked for HOURS with no notification, because a parked agent
never emits a stop for the drain to ride on.

Detection without delivery is the whole failure — so this is the delivery half. It
does NOT wait to be pulled: it SCRAPES worker panes and, for a newly-blocked one,
PUSHES a line into the coordinator's pane. That push reaches the coordinator's
model as its next input, so a heads-down admin is interrupted with the fact rather
than discovering it on a sweep it has to remember to run.

WHY A SWEEP AND NOT WAKE-ON-STOP. The headline case — a worker frozen on a picker —
emits NO stop event: the turn never ends, so there is nothing to hang a
wake-on-persist off. Only something OUTSIDE the frozen worker can notice it, and
that is a periodic scrape. wake-on-stop would cover the lesser "stopped holding
work" case and miss the exact one the bead is named for.

TWO INVARIANTS, both learned expensively in this repo:

  DEDUP OR IT IS A SPAM CHANNEL. A sweep that re-sends "X is blocked" every
  interval trains the coordinator to ignore it — vigilance-fatigue is the same
  class of failure as the invisibility it replaces. So a worker is notified ONCE
  per block episode; the record is cleared when it un-blocks, so a LATER block
  notifies again. The state is durable (a file), so the dedup survives the sweep
  process restarting — otherwise every restart re-spams.

  PUSH ONLY WHAT WAS MEASURED. `waiting` is a live pane verdict; a worker read as
  blocked was blocked at scrape time, and the message says so plainly with the
  route to look (`st log <worker>`). It never asserts a state it did not see.
"""
from __future__ import annotations
import json
from pathlib import Path

from . import triage as triage_mod
from .protocols import Agent
from .runtime import asks_a_question, auth_expired
from .tier import route_stop


# The states a worker can be in that the COORDINATOR must act on and the worker
# cannot resolve alone. `waiting` is the bead's headline: a blocking picker, which
# never times out and never emits a stop. Kept as a set so the escalation of what
# counts as "needs the coordinator" is one edit, in one place.
ACTIONABLE = frozenset({triage_mod.WAITING})


def blocked_workers(agents, panes, runtime):
    """Every up worker whose live pane reads as an ACTIONABLE block, by name.

    A scrape, not a stored verdict — the same reading `st crew` shows, taken now.
    Only workers: a lead or administrator blocking is a different problem (there
    is nobody above them to wake), and this function's whole job is "who does the
    coordinator need to hear about".
    """
    out = []
    for ag in sorted(agents, key=lambda a: a.name):
        if ag.role != "worker" or not ag.pane or not panes.exists(ag.pane):
            continue
        screen = panes.capture(ag.pane, attrs=True)
        plain = triage_mod.strip_attrs(screen)
        state = triage_mod.work_state(
            screen, runtime.shows_ready_ui(plain),
            awaiting=asks_a_question(runtime, plain),
            auth_dead=auth_expired(runtime, plain))
        if state in ACTIONABLE:
            out.append((ag.name, state))
    return out


def wake_recipient(reg, panes, worker: str, message: str) -> str | None:
    """Deliver `message` into the pane of whoever `worker`'s stops route to.

    route_stop ALREADY resolves that recipient (the lead, or the administrator
    when there is no lead / the lead is down) — the same destination the worker's
    own stop events go to. Reusing it means the notification and the stop stream
    agree about who is watching this worker, so a re-parented worker's alerts
    follow it without a second rule to keep in sync.

    Returns the recipient's name on a delivered push, or None when there was
    nowhere reachable to send it — never a silent success. A push into a pane that
    does not exist is not a notification.
    """
    try:
        routing = route_stop(reg, worker)
    except LookupError:
        return None
    try:
        recipient = reg.get(routing.to)
    except LookupError:
        return None
    if not recipient.pane or not panes.exists(recipient.pane):
        return None
    panes.send(recipient.pane, message)
    return recipient.name


def _message(worker: str, state: str) -> str:
    return (f"⚠ {worker} is BLOCKED ({state}) and needs you — it will NOT time out "
            f"or self-resolve. Look: `st log {worker}`. Answer the prompt, or tell "
            f"it to put the decision on its bead with a recommendation and carry "
            f"on. (auto-notice from st tend; you were not asked to sweep.)")


def saturated_agents(agents, panes, runtime):
    """Up agents whose live pane reads SATURATED — idle AND past the cycle
    threshold (aegis-bik9).

    Only SATURATED, which work_state derives in the IDLE branch, so it is already
    "idle and over the threshold": a busy agent past the threshold reads `busy`
    (its "/clear to save Nk" footer is replaced by the spinner mid-turn, so the
    number is unreadable), and we never interrupt a working agent. The cycle
    prompt lands exactly on the agent that is idle-and-refused — the one that most
    needs it and can act on it now. Every role, not just workers: a saturated
    coordinator must cycle too.
    """
    out = []
    for ag in sorted(agents, key=lambda a: a.name):
        if not ag.pane or not panes.exists(ag.pane):
            continue
        screen = panes.capture(ag.pane, attrs=True)
        plain = triage_mod.strip_attrs(screen)
        # auth_dead (aegis-arma): a saturated pane whose login expired must NOT
        # be prompted to cycle — measured: tend's cycle driver prompted one over
        # and over, and every prompt died against the very banner it could not
        # see, filling the dead pane's scrollback with instructions. AUTH_DEAD
        # outranks SATURATED in work_state, so it falls out here.
        state = triage_mod.work_state(
            screen, runtime.shows_ready_ui(plain),
            awaiting=asks_a_question(runtime, plain),
            auth_dead=auth_expired(runtime, plain))
        if state == triage_mod.SATURATED:
            out.append(ag.name)
    return out


def _cycle_message() -> str:
    # An INSTRUCTION the agent executes, NOT a bare `/clear` keystroke. The agent
    # checkpoints FIRST, then clears — a raw /clear would drop unsaved work
    # (h562's rule). Pushed as a user turn to the agent's own Claude, which then
    # does checkpoint -> /clear -> resume, in that order.
    return (
        "⚠ st tend: you are PAST THE 400k CYCLE THRESHOLD. CYCLE NOW, and in this "
        "order: (1) CHECKPOINT — write your current state to your active bead "
        "(what you are mid-task on, decisions already made, the exact next step) "
        "with `bd comment <id> --file <notes>`; (2) THEN run /clear to reset "
        "context; (3) THEN resume from the bead. Do the checkpoint BEFORE /clear "
        "— a bare /clear loses whatever was not written down. (auto-prompt from "
        "st tend, once per saturation episode.)")


def push_to_own_pane(reg, panes, agent: str, message: str) -> str | None:
    """Deliver `message` into the AGENT'S OWN pane (aegis-bik9) — the cycle remedy
    goes to the saturated agent itself, not to a coordinator. Returns the agent
    name on a delivered push, None when its pane is unreachable (a failed push
    stays pending, never a silent success)."""
    try:
        card = reg.get(agent)
    except LookupError:
        return None
    if not card.pane or not panes.exists(card.pane):
        return None
    panes.send(card.pane, message)
    return agent


class CycleDriver:
    """DRIVE the cycle, not just flag it (aegis-bik9). h562 detects + refuses a
    saturated agent, but the remedy — checkpoint-to-bead then /clear — had no
    delivery path, so a coordinator raw-tmux'd it by hand to three agents. This
    pushes the checkpoint-then-clear INSTRUCTION to a saturated idle agent's own
    pane automatically, so the agent cycles itself. It never sends a bare /clear.

    Same dedup discipline as the blocked-worker push: ONCE per saturation episode
    (a durable ledger, so a heartbeat does not re-prompt every interval and a
    sweeper restart does not re-spam), re-armed when the agent drops back below the
    threshold — so a later saturation prompts again. Fail-open: an unreachable pane
    is retried next sweep, never swallowed.
    """

    def __init__(self, root, reg, panes, *, push=push_to_own_pane, wiring=None,
                 refresh=None, log=None):
        self.path = Path(root) / "notify" / "cycling.json"
        self._reg = reg
        self._panes = panes
        self._push = push
        # agent -> LiveWiring | None. Injected so a test models wired/dark
        # without composing launch lines; the default reads the LIVE process.
        self._wiring_fn = wiring or self._wiring
        # workspace-path -> error | None. Keep-current at the cycle (aegis-4zld):
        # the agent is about to /clear, and the fresh context must read a CURRENT
        # tree — a cycle onto a stale one re-derives against code that already
        # changed. None = no pulling (tests, dry contexts).
        self._refresh = refresh
        self._log = log or (lambda msg: None)

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, ledger: dict) -> None:
        from .files import write_json_atomic
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, ledger)

    def sweep(self, agents, runtime) -> list[str]:
        """One pass. PROMPT each newly-saturated agent to cycle, re-arm any that
        recovered, and return the names actually prompted (empty when none are
        newly saturated — the quiet, common case)."""
        saturated = set(saturated_agents(agents, self._panes, runtime))
        ledger = self._load()
        prompted = []

        # Re-arm: an agent no longer saturated (it cycled, or dropped below) is
        # forgotten, so its next saturation prompts again.
        for agent in list(ledger):
            if agent not in saturated:
                del ledger[agent]

        for agent in sorted(saturated):
            if ledger.get(agent) == "saturated":
                continue                       # already prompted this episode
            # DARK AGENTS ARE NOT ST'S TO DRIVE (aegis-arma follow-up, measured:
            # the live loop typed cycle prompts into foreign gastown-launched
            # panes — sessions st did not launch, whose processes carry no
            # stop_event wiring — over and over; one of them was also auth-dead,
            # so the prompts piled onto a login banner). Same definition of dark
            # as feed_check's free list: no readable shantytown wiring on the
            # LIVE process. Unreadable counts as dark — the safe direction is
            # not typing into a pane whose process you cannot read. Ledgered as
            # "dark" so the skip is SAID once per episode, not every 30s — but
            # re-CHECKED every sweep, so an agent relaunched into wiring while
            # still saturated is prompted, not stuck behind an old verdict.
            wiring = self._wiring_fn(agent)
            if wiring is None or not wiring.directions:
                if ledger.get(agent) != "dark":
                    self._log(f"cycle: {agent} is saturated but DARK (no stop "
                              f"wiring on its live process — a foreign "
                              f"launcher's agent) — not st's to drive, skipping")
                ledger[agent] = "dark"
                continue
            # KEEP CURRENT AT THE CYCLE (aegis-4zld): the agent is idle-saturated
            # — a safe moment — and about to /clear. Pull ff-only BEFORE the
            # prompt lands so the post-clear context starts on a current tree.
            # A refused pull never blocks the cycle (the /clear matters more),
            # but it is LOUD: cycling onto known-stale code is worth a line.
            if self._refresh is not None:
                try:
                    card = self._reg.get(agent)
                    if card.workspace:
                        if err := self._refresh(card.workspace):
                            self._log(f"cycle: {agent}'s workspace was NOT "
                                      f"brought current (ff-only refused: "
                                      f"{err.splitlines()[0]}) — cycling on the "
                                      f"existing tree")
                except Exception as e:  # noqa: BLE001 — pull is best-effort
                    self._log(f"cycle: keep-current for {agent} errored ({e!r}) "
                              f"— cycling on the existing tree")
            target = self._push(self._reg, self._panes, agent, _cycle_message())
            if target is None:
                self._log(f"cycle: {agent} is saturated but its pane was "
                          f"unreachable — NOT prompted, will retry")
                continue
            ledger[agent] = "saturated"
            prompted.append(agent)
            self._log(f"cycle: prompted {agent} to checkpoint + /clear")

        self._save(ledger)
        return prompted

    def _wiring(self, agent: str):
        """The live wiring of `agent`'s pane process, or None (= unreadable,
        which the caller treats as dark — never as fine)."""
        from .runtime import live_wiring
        try:
            card = self._reg.get(agent)
        except LookupError:
            return None
        if not card.pane:
            return None
        reader = getattr(self._panes, "cmdline", None)
        if reader is None:
            return None
        return live_wiring(card.pane, reader)


class Notifier:
    """The dedup ledger + the push. A worker is woken-about ONCE per block episode.

    The ledger is a single json map {worker: state} of who has an OUTSTANDING,
    already-delivered notification. A sweep notifies a blocked worker only if it
    is NOT in the ledger; it drops a worker from the ledger the moment it is no
    longer blocked, which re-arms it for a future block.
    """

    def __init__(self, root, reg, panes, *, wake=wake_recipient, log=None):
        self.path = Path(root) / "notify" / "blocked.json"
        self._reg = reg
        self._panes = panes
        self._wake = wake
        self._log = log or (lambda msg: None)

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, ledger: dict) -> None:
        from .files import write_json_atomic
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, ledger)

    def sweep(self, agents, runtime) -> list[str]:
        """One pass. PUSH each newly-blocked worker to its coordinator, clear the
        ledger of any that recovered, and return the names actually notified this
        pass (empty when nothing was newly blocked — the quiet, common case)."""
        blocked = dict(blocked_workers(agents, self._panes, runtime))
        ledger = self._load()
        notified = []

        # Re-arm: anyone in the ledger who is no longer blocked gets forgotten, so
        # their NEXT block notifies. Done first, so a worker that unblocked and
        # re-blocked within one interval is still treated as a fresh episode.
        for worker in list(ledger):
            if worker not in blocked:
                del ledger[worker]

        for worker, state in sorted(blocked.items()):
            if ledger.get(worker) == state:
                continue                       # already delivered this episode
            recipient = self._wake(self._reg, self._panes, worker,
                                   _message(worker, state))
            if recipient is None:
                # Nowhere to send it. Do NOT record it as notified — a failed push
                # must stay pending so a later sweep retries, not be swallowed as
                # done. Loud, because a coordinator with no reachable pane is its
                # own problem the operator must see.
                self._log(f"notify: {worker} is {state} but its coordinator pane "
                          f"was unreachable — NOT delivered, will retry")
                continue
            ledger[worker] = state
            notified.append(worker)
            self._log(f"notify: woke {recipient} — {worker} is {state}")

        self._save(ledger)
        return notified


def push_to_admin(reg, panes, message: str) -> str | None:
    """Deliver `message` into the ADMINISTRATOR's pane (aegis-nk0e). The idle-fleet
    alert goes to the coordinator whose job is dispatch — the one person who is
    part of the failure mode and would otherwise have to remember to sweep.
    Returns the admin name on a delivered push, None when there is no admin or its
    pane is unreachable (a failed push stays pending, never a silent success)."""
    from .tier import _find_administrator
    admin = _find_administrator(reg)
    if not admin:
        return None
    try:
        card = reg.get(admin)
    except LookupError:
        return None
    if not card.pane or not panes.exists(card.pane):
        return None
    panes.send(card.pane, message)
    return admin


class IdleFleetAlerter:
    """PUSH the coordinator when FREE feedable workers and DISPATCHABLE beads
    coexist — the NEGLECTED state (aegis-nk0e), the soft sibling of hfta's hard
    gate. The coordinator stalling — handling one question and stopping while nine
    agents sat idle with a full ready queue — is the same class of bug as a blocked
    worker being invisible, and the fix is the same: PUSH, do not rely on the
    coordinator remembering to read a free-count nobody is obliged to look at.

    It REUSES feed_check's free-feedable + dispatchable computation exactly, so the
    soft push and the hard gate agree on who is free and what is ready — no second
    opinion. And it reuses the blocked-worker push's dedup: alert once per idle
    EPISODE per worker (re-armed when the worker stops being free), so a still-idle
    fleet does not re-spam every interval but a NEWLY-idle agent does.

    FAIL OPEN: any error (tmux, bd, registry) pushes nothing and returns []. A
    broken detector must never block a stop or a dispatch — it just goes quiet.
    """

    def __init__(self, root, reg, panes, runtime, *, push=push_to_admin,
                 bd_ready=None, log=None):
        self.path = Path(root) / "notify" / "idle_fleet.json"
        self._reg = reg
        self._panes = panes
        self._runtime = runtime
        self._push = push
        # Injected so a test drives it without bd; the default resolves bd's
        # store from the ADMIN's workspace, never the ambient cwd — the live
        # tend loop ran from a directory with no beads store, `bd ready` raised
        # on every sweep, and this alerter's fail-open ate it: nk0e never fired
        # once in two days (aegis-arma follow-up, measured).
        from . import feed_check
        self._bd_ready = bd_ready or (
            lambda: feed_check._bd_ready(feed_check.bd_cwd(reg)))
        self._log = log or (lambda msg: None)

    def _load(self) -> list:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []

    def _save(self, alerted: list) -> None:
        from .files import write_json_atomic
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, sorted(alerted))

    def sweep(self, agents) -> list[str]:
        """One pass. Idle workers split by WHO their next work belongs to
        (aegis-wjgt groundwork):

        - UNHAULING idle + UNASSIGNED ready work -> ONE idle-fleet alert to the
          coordinator (unchanged nk0e behavior, minus the workers below).
        - HAULING idle (ready beads already ASSIGNED to them) -> the COORDINATOR
          HEARS NOTHING; the WORKER gets a self-feed nudge instead, once per idle
          episode. This is the haul design's core ask ("without notifying the
          coordinator") and, until the stop-hook advance lands, the BELT that
          keeps an excluded worker from stalling silently — no coordinator ping
          may ever mean nobody-pings. It survives as the fallback layer under
          the advance hook (tend catches what a missed stop event would drop).

        Returns the newly-idle names alerted/nudged this pass. Fully fail-open."""
        from . import feed_check
        try:
            free = feed_check.free_feedable_workers(self._reg, self._panes, self._runtime)
        except Exception:
            return []                              # detector broke -> stay quiet
        already = set(self._load())

        # Re-arm: a worker no longer free is forgotten, so a LATER idle episode
        # alerts again. Done first, so a fleet that emptied and re-filled is fresh.
        already &= set(free)

        newly = [w for w in free if w not in already]
        if not newly:
            self._save(already)                    # still-idle set -> no re-spam
            return []

        # bd is the one external call; a hiccup FAILS OPEN (no push, no record —
        # so it retries next pass), never a block.
        try:
            ready_beads = self._bd_ready()
        except Exception:
            return []
        queues = feed_check.hauls(ready_beads)
        hauling_newly = [w for w in newly if w in queues]
        unhauled_free = [w for w in free if w not in queues]
        newly = [w for w in newly if w not in queues]

        # The worker-side nudge: their queue is loaded; feed themselves.
        nudged = []
        for worker in hauling_newly:
            beads = queues[worker]
            target = push_to_own_pane(self._reg, self._panes, worker,
                                      _self_feed_message(worker, beads))
            if target is None:
                self._log(f"haul: {worker} is idle with {len(beads)} assigned "
                          f"ready bead(s) but its pane was unreachable — NOT "
                          f"nudged, will retry")
                continue
            nudged.append(worker)
            self._log(f"haul: nudged {worker} to self-feed ({len(beads)} "
                      f"assigned ready: {', '.join(beads[:3])}) — coordinator "
                      f"deliberately not pinged")

        ready = feed_check.dispatchable(set(unhauled_free), ready_beads)
        if not newly or not ready:
            # Nothing for the coordinator this pass. Record who was HANDLED
            # (still-idle already + the nudged), so a still-idle hauling worker
            # is not re-nudged every interval; an un-nudged one stays pending.
            self._save(sorted(already | set(nudged)))
            return nudged

        admin = self._push(self._reg, self._panes,
                           _idle_fleet_message(unhauled_free, newly, ready))
        if admin is None:
            self._log("idle-fleet: free workers + ready work, but no reachable "
                      "coordinator pane — NOT alerted, will retry")
            self._save(sorted(already | set(nudged)))
            return nudged
        self._save(sorted(already | set(nudged) | set(unhauled_free)))
        self._log(f"idle-fleet: alerted {admin} — {len(unhauled_free)} idle, "
                  f"{len(ready)} ready")
        return newly + nudged


def _self_feed_message(worker: str, beads: list[str]) -> str:
    # The design citation lives HERE, not in the emitted string (the ratchet's
    # rule, which fired on the first draft of this very message): the thread
    # design bead is aegis-wjgt.
    top = ", ".join(beads[:3])
    return (
        f"⚠ st tend: you are IDLE with {len(beads)} ready item(s) already assigned "
        f"to you ({top}). Your queue self-feeds — run `bd ready`, take the top "
        f"assigned item, and continue. The coordinator was deliberately NOT "
        f"pinged: assigned work is yours to advance, not theirs to re-dispatch. "
        f"(auto-nudge from st tend, once per idle episode.)")


def _idle_fleet_message(free: list[str], newly: list[str], ready) -> str:
    top = "; ".join(f"{bid} {title}"[:60] for bid, title in ready[:3])
    fresh = f" (newly idle: {', '.join(newly)})" if newly != free else ""
    return (
        f"⚠ st tend — RULE ZERO: {len(free)} feedable worker(s) IDLE "
        f"({', '.join(free)}){fresh} with {len(ready)} dispatchable bead(s) ready. "
        f"DISPATCH — a free worker while work is ready is the coordinator's stall. "
        f"`st go <bead> <worker>`. Top ready: {top}. "
        f"(auto-alert from st tend; you were not asked to sweep.)")
