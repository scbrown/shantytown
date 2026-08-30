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
import os
import time
from pathlib import Path
from typing import NamedTuple

from . import triage as triage_mod
from .attribution import ST_TEND, attribute
from .tmux import PaneNotAgent
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
    # ATTRIBUTED (aegis-5vxmz). This lands in a coordinator's pane in the
    # imperative — "⚠ <worker> is BLOCKED and needs you" — at the same prompt
    # the operator types at. Unsigned, it reads as Stiwi telling them to go look.
    try:
        panes.send(recipient.pane, attribute(message, ST_TEND))
    except PaneNotAgent:
        # Its runtime has exited: the pane is a shell and typing here would
        # EXECUTE this notification (aegis-ikj4t). Not notified — which is
        # exactly what None already means to every caller.
        return None
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


class PaneRead(NamedTuple):
    """One pane's live reading: what state it is in, and what its context depth
    MEASURED as — `depth_k = None` meaning the depth could not be read, which is
    a different thing from a low depth and must never collapse into one."""
    state: str
    depth_k: float | None


def agent_states(agents, panes, runtime) -> dict:
    """name -> PaneRead, for every agent with a readable pane.

    `saturated_agents` computes exactly this and then throws the state away,
    keeping only the SATURATED names. That discard is what broke the cycle
    ledger's dedup (internal-ref): "not in the saturated set" was read as
    "recovered", and it is not — a BUSY agent past the threshold also leaves
    that set, and while it is busy its context depth is UNREADABLE (the
    "/clear to save Nk" footer is replaced by the spinner, as saturated_agents'
    own docstring says).

    So the state is returned rather than discarded, and the caller decides what
    counts as recovery. An agent with no pane, or an unreadable one, is simply
    absent from the map — cannot-tell is not a state, and inventing one here is
    what the caller must not be allowed to do.

    THE DEPTH RIDES ALONGSIDE THE STATE, and that is the whole point of PaneRead
    (aegis-rfz1b). `IDLE` is NOT evidence that an agent is under the cycle
    threshold: work_state returns IDLE both when the depth was READ and found
    low, and when the depth could not be read at all — `context_tokens_k` returns
    None whenever the "/clear to save Nk" footer is replaced by the spinner. A
    caller that re-armed on IDLE was therefore treating cannot-tell as recovered,
    which is the exact error the paragraph above describes, one field over. So
    `depth_k` is None for "not measured" and a number for "measured", and a
    caller deciding recovery must look at THAT, never at the state alone.
    """
    out = {}
    for ag in agents:
        if not ag.pane or not panes.exists(ag.pane):
            continue
        screen = panes.capture(ag.pane, attrs=True)
        plain = triage_mod.strip_attrs(screen)
        out[ag.name] = PaneRead(
            state=triage_mod.work_state(
                screen, runtime.shows_ready_ui(plain),
                awaiting=asks_a_question(runtime, plain),
                auth_dead=auth_expired(runtime, plain)),
            depth_k=triage_mod.context_tokens_k(screen))
    return out


def _cycle_message() -> str:
    # An INSTRUCTION the agent executes, NOT a bare `/clear` keystroke. The agent
    # checkpoints FIRST — a raw /clear drops unsaved work (h562's rule). Pushed as
    # a user turn to the agent's own Claude.
    #
    # IT NO LONGER PRESCRIBES `/clear`, AND THAT IS THE POINT (aegis-3laza).
    #
    # This message used to end "run /clear to reset context", and that instruction
    # is measurably harmful: `/clear` DROPS THE SESSION OUT OF BYPASS INTO MANUAL.
    # Measured on malcolm — clearing a saturated agent fixed the context and
    # created a second blocker, after which `st crew` correctly reported it
    # not-reliably-dispatchable. So the automatic remedy needed its own remedy, and
    # this driver was handing it out on a timer, fleet-wide, twelve times in one
    # session.
    #
    # `st cycle --self` records a request that `st tend` honours by STOP + RELAUNCH
    # instead, which restores what /clear destroys: bypass, the MCP kit, skills,
    # journaling, and a verification that the stop hooks are live on the new
    # process. The agent keeps working until tend picks the request up — nothing is
    # lost if it never fires.
    #
    # SHORTENED and moved to handoff_text (aegis-x6yoq). This was ~110 words and
    # fires on a timer; Stiwi's ask was to cut the recurring pane essays. The
    # rationale above is preserved HERE, in the code, and for agents it now lives
    # in `st help handoff` — written once and read on demand, rather than
    # re-pushed into every pane every few minutes. A message that long is skimmed,
    # which is how the one safety-critical sentence in it gets skipped.
    from . import handoff_text
    from .triage import CYCLE_THRESHOLD_K
    return handoff_text.cycle_now(None, None).replace(
        "past the cycle line", f"past the {int(CYCLE_THRESHOLD_K)}k cycle line")


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
    # ATTRIBUTED (aegis-5vxmz) — and this is the one that most needed it. The
    # cycle prompt orders an agent to CHECKPOINT AND /clear, and the haul
    # messages that also ride this helper hand out work. Both are exactly the
    # kind of instruction an agent would obey without question BECAUSE it looked
    # like it came from the operator.
    try:
        panes.send(card.pane, attribute(message, ST_TEND))
    except PaneNotAgent:
        return None
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
        self._root = root          # aegis-mxgzh: the sweeps need it to resolve the br backend
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
        states = agent_states(agents, self._panes, runtime)
        saturated = {n for n, r in states.items()
                     if r.state == triage_mod.SATURATED}
        ledger = self._load()
        prompted = []

        # RE-ARM ONLY ON A POSITIVE READING OF RECOVERY (internal-ref).
        #
        # This used to be "not in the saturated set -> forget it", and that is
        # DEFEATED BY THE AGENT WORKING. SATURATED is derived in the IDLE
        # branch; a busy agent past the threshold reads `busy`, and while it is
        # busy its context depth is unreadable. So the moment a prompted agent
        # started doing anything — including the cycling it was just told to do
        # — it left the set, its entry was deleted, and its next idle moment
        # prompted it again.
        #
        # MEASURED on the live fleet: 12 cycle prompts sent, with arnold at
        # 07:56 and 09:14, malcolm at 07:26 and 08:44, dearing at 07:50 and
        # 09:08 — a ~78-minute re-prompt cadence on a mechanism whose own
        # message says "once per saturation episode".
        #
        # The shape is cannot-tell read as a pass: unreadable depth was treated
        # as recovered. So recovery must now be OBSERVED — the agent is idle and
        # no longer over the threshold. Busy, queued, waiting, auth-dead and
        # pane-unreadable all KEEP the entry, because none of them is evidence
        # the context was cleared.
        # RE-ARM ONLY ON A MEASURED DEPTH BELOW THE THRESHOLD (aegis-rfz1b).
        #
        # This used to re-arm on `state == IDLE`, and IDLE is not evidence of
        # recovery: work_state returns it BOTH when the depth was read and found
        # low AND when the depth could not be read at all, because
        # context_tokens_k returns None while the "/clear to save Nk" footer is
        # replaced by the spinner. So a pane caught mid-transition read IDLE, the
        # entry was deleted, the very next sweep saw SATURATED again, and the
        # agent was re-prompted — once per turn boundary, forever. Measured: the
        # coordinator received the cycle instruction four times in one session
        # having checkpointed after the first.
        #
        # That is the SAME defect the paragraph above describes and claims to have
        # fixed. It stopped trusting "absent from the saturated set" precisely
        # because absence could mean unreadable, then trusted IDLE — which is the
        # bucket unreadable falls into. Cannot-tell was promoted to a verdict
        # twice, in two different fields.
        #
        # `depth_k is None` therefore means CANNOT TELL and must leave the ledger
        # exactly as it is. Only a number we actually read, and read below the
        # threshold, retires an episode.
        for agent in list(ledger):
            read = states.get(agent)
            if read is None or read.depth_k is None:
                continue                       # no pane, or depth unreadable
            if read.depth_k < triage_mod.CYCLE_THRESHOLD_K:
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
            self._log(f"cycle: prompted {agent} to checkpoint + st cycle --self")

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
        self._root = root          # aegis-mxgzh: the sweeps need it to resolve the br backend
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
    try:
        panes.send(card.pane, attribute(message, ST_TEND))   # aegis-5vxmz
    except PaneNotAgent:
        return None
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
                 bd_ready=None, bd_in_progress=None, context_k=None,
                 handoff_k=None, log=None, audit=None, input_preflight=None,
                 turn_receipts=None):
        self._root = root          # aegis-mxgzh: the sweeps need it to resolve the br backend
        self.path = Path(root) / "notify" / "idle_fleet.json"
        # Kept for the launch-stamp ownership gate (aegis-2j2r): tend must
        # only feed agents st launched, same signal as the hard gate's.
        self._shanty_root = Path(root)
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
            lambda: feed_check._bd_ready(feed_check.bd_cwd(reg), root=root, reg=reg))
        self._bd_in_progress = bd_in_progress or (
            lambda cwd: feed_check.bd_in_progress(cwd, root=root, reg=reg))
        # The worker's context depth off its live pane (the same footer read
        # saturation uses); injected for tests. None = unreadable = never over.
        self._context_k = context_k or self._pane_context_k
        from .stop_event import HAUL_HANDOFF_K
        self._handoff_k = handoff_k if handoff_k is not None else HAUL_HANDOFF_K
        self._log = log or (lambda msg: None)
        from .feed_audit import FeedAudit
        self._audit = audit or FeedAudit(Path(root))
        if turn_receipts is None:
            from .feed_audit import codex_turn_starts
            self._turn_receipts = lambda: codex_turn_starts(
                Path.home() / ".codex" / "sessions")
        else:
            self._turn_receipts = turn_receipts
        self._input_preflight = input_preflight or self._pane_input_preflight

    def _pane_input_preflight(self, worker: str):
        """Return the evidence-bearing input verdict immediately before a feed."""
        from . import input_box
        from .runtime import asks_a_question
        card = self._reg.get(worker)
        screen = self._panes.capture(card.pane, attrs=True)
        plain = triage_mod.strip_attrs(screen)
        awaiting = (False if self._runtime is None
                    else bool(asks_a_question(self._runtime, plain)))
        return input_box.show(self._panes, card.pane, awaiting=awaiting)

    def _load(self) -> list:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []

    def _save(self, alerted: list) -> None:
        from .files import write_json_atomic
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, sorted(alerted))

    def _pane_context_k(self, worker: str) -> float | None:
        """The worker's context depth off its live pane — the same footer read
        saturation uses. None on any failure: unknown is never over the line."""
        try:
            card = self._reg.get(worker)
            if not card.pane:
                return None
            return triage_mod.context_tokens_k(
                triage_mod.strip_attrs(self._panes.capture(card.pane, attrs=True)))
        except Exception:
            return None

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
        from .stop_event import _stood_down
        window_id = self._audit.begin()
        backend = feed_check.backend_kind(self._root, self._reg)
        try:
            unacked = self._audit.reconcile_turn_starts(self._turn_receipts())
            for serve_id in unacked:
                self._log(f"haul: serve {serve_id} is START_UNACKNOWLEDGED — "
                          "input was sent but no matching Codex turn began")
        except Exception as exc:  # evidence loss must not stop the feed
            self._log(f"haul: turn receipt reconciliation unavailable ({exc!r})")
        if _stood_down(self._shanty_root):
            self._log("idle-fleet: fleet stood down — correctly not alerting")
            return []
        try:
            free = feed_check.free_feedable_workers(self._reg, self._panes, self._runtime,
                                                    root=self._shanty_root)
        except Exception:
            return []                              # detector broke -> stay quiet
        # Read anchors before the idle ledger: an idle Codex continuation is
        # deliberately absent from free_feedable_workers (it already owns
        # work), but is exactly the state this fallback must recover.
        try:
            cwd = feed_check.bd_cwd(self._reg)
            active = self._bd_in_progress(cwd)
            self._audit.record(window_id, leg="anchor", backend=backend,
                               attempted=True, acted_on=True,
                               reason=f"read {len(active)} active item(s)")
        except Exception as exc:      # noqa: BLE001
            self._audit.record(window_id, leg="anchor", backend=backend,
                               attempted=True, refused=True,
                               reason=f"{type(exc).__name__}: {exc}")
            active = []
        resumable = feed_check.idle_resumable_codex(
            self._reg, self._panes, self._runtime, active,
            root=self._shanty_root)
        observed_idle = sorted(set(free) | set(resumable))
        already = set(self._load())

        # Re-arm: a worker no longer free is forgotten, so a LATER idle episode
        # alerts again. Done first, so a fleet that emptied and re-filled is fresh.
        already &= set(observed_idle)

        newly = [w for w in observed_idle if w not in already]
        if not newly:
            self._save(already)                    # still-idle set -> no re-spam
            return []

        # bd is the one external call; a hiccup FAILS OPEN (no push, no record —
        # so it retries next pass), never a block.
        try:
            ready_beads = self._bd_ready()
            self._audit.record(window_id, leg="feed", backend=backend,
                               attempted=True, acted_on=True,
                               reason=f"read {len(ready_beads)} ready item(s)")
        except Exception as exc:
            self._audit.record(window_id, leg="feed", backend=backend,
                               attempted=True, refused=True,
                               reason=f"{type(exc).__name__}: {exc}")
            self._log(f"haul: feed backend read FAILED ({exc!r}) — fail-open; "
                      "no worker fed this pass")
            return []
        # in_progress counts as a haul (aegis-ap4gm) — same reason as the
        # feed_check gate: an item the worker already started is its next work,
        # and `bd ready` structurally cannot report it.
        # in_progress counts as a haul (aegis-ap4gm) — same reason as the
        # feed_check gate: an item the worker already started is its next work,
        # and `bd ready` structurally cannot report it. Fails open.
        queues = feed_check.hauls(ready_beads, active)
        hauling_newly = [w for w in newly if w in queues]
        unhauled_free = [w for w in free if w not in queues]
        newly = [w for w in newly if w not in queues]

        # TEND IS THE SECOND ADVANCE TRIGGER (the already-idle gap): the stop
        # hook advances a worker AT a stop, but an ALREADY-IDLE worker never
        # stops again on its own — a queue loaded after it idled sat until a
        # human bootstrapped it (measured at first fleet queue-load). So tend
        # FEEDS the idle hauler its actual next bead — same message, same
        # claim, same handoff line as the stop-hook advance (one voice,
        # feed_check's) — never a generic "go look" nudge, and never a
        # coordinator ping. Guards, in order:
        #   - an UNREADABLE in_progress set feeds NOBODY (cannot tell -> do
        #     not guess);
        #   - an OPEN ANCHOR does NOT block the feed (aegis-u13t): the pane is
        #     IDLE, so the anchor is not being worked NOW — it is a design
        #     pending human review, parked on a HITL blocker, or a forgotten
        #     close, and every one of those wedged the queue while tend logged
        #     "not fed" forever and the coordinator got pinged (the exact toil
        #     gez6 removes). The worker's open anchors are only excluded from
        #     what gets FED — never re-feed a bead the worker already holds.
        #     Drain safety is the newly-idle dedup below (`already`), which
        #     bounds tend to ONE feed per idle episode with or without an
        #     anchor guard;
        #   - past the HANDOFF LINE the feed becomes the checkpoint+/clear
        #     instruction (the same 60%-of-window line the stop hook applies).
        nudged = []
        if hauling_newly:
            try:
                cwd = feed_check.bd_cwd(self._reg)
                open_anchors: dict[str, set[str]] | None = {}
                for b in self._bd_in_progress(cwd):
                    w = (b.get("assignee") or "").split("/")[-1]
                    if w:
                        open_anchors.setdefault(w, set()).add(b.get("id"))
            except Exception:
                open_anchors = None            # could not tell -> feed nobody
        for worker in hauling_newly:
            beads = queues[worker]
            if open_anchors is None:
                self._log(f"haul: {worker} idle with {len(beads)} queued but "
                          f"anchor state unreadable — not fed this pass")
                continue
            own_active = [b for b in active
                          if (b.get("assignee") or "").split("/")[-1] == worker]
            if worker in resumable and own_active:
                bead = own_active[0]
                target = push_to_own_pane(
                    self._reg, self._panes, worker,
                    feed_check.haul_resume_message(
                        bead.get("id", "?"), bead.get("title") or ""))
                if target is not None:
                    nudged.append(worker)
                    self._log(f"haul: resumed idle Codex {worker} on active "
                              f"anchor {bead.get('id', '?')}")
                continue
            feedable = [b for b in beads
                        if b not in open_anchors.get(worker, ())]
            if not feedable:
                self._log(f"haul: {worker} idle but every queued bead is its "
                          f"own open anchor — not fed this pass")
                continue
            # THE SESSION CEILING, ON THIS TRIGGER TOO (aegis-xxae9). ONE
            # advance, TWO triggers — the same reason haul_feed_message itself
            # is shared. A ceiling enforced only at the worker's own stop would
            # be bypassed here every time: tend feeds workers that are ALREADY
            # idle, which is exactly the state a worker is in after the ceiling
            # told it to stop. The gate has to sit on both or it sits on
            # neither.
            from . import session_budget as sb
            limits, spend, ceiling = sb.gate(self._shanty_root, worker)
            if ceiling is not None:
                self._log(f"haul: {worker} is over its session ceiling "
                          f"({ceiling.label()}) — NOT fed; it has been asked to "
                          f"report and stop")
                if not sb.already_reported(self._shanty_root, worker, spend):
                    sb.mark_reported(self._shanty_root, worker, spend)
                    push_to_own_pane(self._reg, self._panes, worker,
                                     sb.stop_message(ceiling))
                continue
            if limits.active and spend.signal_lost:
                self._log(sb.signal_lost_note(limits, spend, worker))

            delivery_item = ""
            if (ck := self._context_k(worker)) is not None and ck >= self._handoff_k:
                message = feed_check.haul_handoff_message(ck, self._handoff_k)
            else:
                nid = feedable[0]
                delivery_item = nid
                serve_id = self._audit.new_serve()
                self._audit.record(window_id, leg="candidate", backend=backend,
                                   worker=worker, item=nid, eligible=True,
                                   reason="idle worker owns ready non-anchor item")
                if self._audit.acted_on(window_id, worker, nid):
                    self._audit.record(window_id, leg="replay", backend=backend,
                                       worker=worker, item=nid, eligible=True,
                                       refused=True,
                                       reason="delivery already acted_on in this window")
                    continue
                try:
                    preflight = self._input_preflight(worker)
                    verdict = preflight.verdict
                except Exception as exc:  # noqa: BLE001 — unknown fails closed
                    verdict = "UNKNOWN"
                    preflight = None
                    detail = f"{type(exc).__name__}: {exc}"
                else:
                    detail = preflight.detail
                from . import input_box
                if verdict not in (input_box.EMPTY, input_box.GHOST):
                    self._audit.record(window_id, leg="input", backend=backend,
                                       worker=worker, item=nid, eligible=True,
                                       attempted=True, refused=True,
                                       reason=f"{verdict}: {detail}".rstrip())
                    self._log(f"haul: {worker} input preflight is {verdict} — "
                              "NOT fed; inspect with `st input " + worker +
                              " --show` and act explicitly")
                    continue
                self._audit.record(window_id, leg="input", backend=backend,
                                   worker=worker, item=nid, eligible=True,
                                   attempted=True, acted_on=True,
                                   reason=f"{verdict} is an empty buffer")
                self._audit.record(window_id, leg="claim", backend=backend,
                                   worker=worker, item=nid, eligible=True,
                                   attempted=True, reason="claim attempted")
                try:
                    feed_check.bd_claim(cwd, nid, root=self._root, reg=self._reg)
                    self._audit.record(window_id, leg="claim", backend=backend,
                                       worker=worker, item=nid, eligible=True,
                                       attempted=True, acted_on=True,
                                       serve_id=serve_id, state="claim_committed",
                                       reason="claim command completed")
                except Exception as exc:
                    self._audit.record(window_id, leg="claim", backend=backend,
                                       worker=worker, item=nid, eligible=True,
                                       attempted=True, refused=True,
                                       reason=f"best-effort claim failed: {type(exc).__name__}: {exc}")
                repeats = sb.times_served(self._shanty_root, worker, nid,
                                          spend.started)
                sb.record_item(self._shanty_root, worker, spend.session, nid)
                message = feed_check.haul_feed_message(
                    nid, "", len(feedable) - 1,
                    headroom=sb.headroom(limits, spend), repeats=repeats)
                message += f" — [st serve:{serve_id} worker:{worker}]"
            target = push_to_own_pane(self._reg, self._panes, worker, message)
            if target is None:
                self._audit.record(window_id, leg="delivery", backend=backend,
                                   worker=worker, item=delivery_item,
                                   eligible=True, attempted=True, refused=True,
                                   reason="pane unreachable")
                self._log(f"haul: {worker} is idle with {len(beads)} assigned "
                          f"ready bead(s) but its pane was unreachable — NOT "
                          f"fed, will retry")
                continue
            nudged.append(worker)
            self._audit.record(
                window_id, leg="delivery", backend=backend, worker=worker,
                item=delivery_item, eligible=bool(delivery_item), attempted=True,
                acted_on=True,
                serve_id=(serve_id if delivery_item else ""),
                state=("input_sent" if delivery_item else ""),
                reason=("natural tend feed delivered" if delivery_item
                        else "handoff instruction delivered; no item fed"))
            self._log(f"haul: fed {worker} its next bead ({feedable[0]}; "
                      f"{len(feedable) - 1} more queued) — coordinator "
                      f"deliberately not pinged")

        # THE GOVERNOR MUST BE ASKED HERE TOO (aegis-yc864, second consumer).
        # `dispatchable()` means open-and-unassigned; it does NOT mean "clears
        # the priority floor". `gate_inputs` wraps this exact call in
        # `throttle()`, and this alerter called the inner half directly — so the
        # docstring above is accurate and is the bug: it reuses feed_check's
        # dispatchable computation EXACTLY, which is the ungoverned half.
        #
        # The cost is not a wrong number. Under a P0-only floor with zero P0
        # beads on the board, this pushed "72 dispatchable — DISPATCH" at the
        # coordinator on a five-minute timer while `st go` refused every one of
        # them, and the refusal text offers "raise its priority" as the way out.
        # That is aegis-diasw's contradiction restored by a second consumer, and
        # a TIMER asking for the priority bump is worse than a hook asking once.
        ready = feed_check.dispatchable(set(unhauled_free), ready_beads)
        ready, held = feed_check.throttle(
            ready, ready_beads, feed_check.governor_admits(self._shanty_root))
        if held and not ready:
            # SILENT IS WRONG, but so is alerting. An idle fleet under an
            # engaged tier is the CORRECT state; the coordinator only needs to
            # be able to tell it from a broken feeder, which is what the log
            # line is for. No push: there is nothing for them to do.
            self._log(f"idle-fleet: {len(held)} bead(s) ready and NONE clear the "
                      f"priority floor — {held[0][2]}. Correctly not alerting.")
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





def _idle_fleet_message(free: list[str], newly: list[str], ready) -> str:
    top = "; ".join(f"{bid} {title}"[:60] for bid, title in ready[:3])
    fresh = f" (newly idle: {', '.join(newly)})" if newly != free else ""
    return (
        f"⚠ st tend — RULE ZERO: {len(free)} feedable worker(s) IDLE "
        f"({', '.join(free)}){fresh} with {len(ready)} dispatchable bead(s) ready. "
        f"DISPATCH — a free worker while work is ready is the coordinator's stall. "
        f"`st go <bead> <worker>`. Top ready: {top}. "
        f"(auto-alert from st tend; you were not asked to sweep.)")


class StalledAlerter:
    """STALLED (aegis-e01l): an agent parked idle while HOLDING an in_progress
    item, with no pane change, no item change and no running shell for the
    whole threshold window — the weaver case: hours at a prompt holding
    aegis-u140 whose blocker had been resolved in a comment it never re-read.
    Every liveness check read fine because nothing measured PROGRESS.

    This is a PROGRESS-OVER-TIME reading, unlike NEGLECTED's point-in-time one:
    each pass snapshots (pane-text hash + sorted held-item ids) per worker and
    compares against the stored episode. ANY change — pane text, held set, a
    live background shell, or the pane simply not being idle — resets the
    episode. Only a snapshot UNCHANGED at every observation for >= threshold
    minutes alerts, once per episode (re-armed by any progress).

    THE DISCRIMINATOR (aegis-mt0r, both directions): a 30-min re-index with a
    live shell reads NOT STALLED — the runtime chrome's shell count (a live
    "· N shells ·") resets the clock. running_shells() returning None is NOT
    treated as a shell: on an idle pane the indicator's absence means no
    shells survive the turn, and treating cannot-see as progress would blind
    the detector exactly like the turn-end-booked-as-task-end defect it
    guards. Chrome that ticks (clocks etc.) makes the hash under-fire — the
    safe direction — stated rather than hidden.

    THRESHOLD, and why the number: default 15 minutes = ~30 consecutive
    unchanged 30s-pass observations. A working agent's pane changes on every
    streamed token, so legitimate silence while idle-and-holding is bounded by
    prompt-render lag (seconds); the measured failure ran HOURS. 15m is two
    orders above the false-positive regime and an order below the harm one.
    Override: SHANTY_STALL_MIN.

    FAIL OPEN, same as every alerter here: any error pushes nothing.
    """

    def __init__(self, root, reg, panes, runtime, *, push=push_to_admin,
                 bd_in_progress=None, threshold_min=None,
                 escalate_after_min=None, now=None, log=None):
        self._root = root          # aegis-mxgzh: the sweeps need it to resolve the br backend
        self.path = Path(root) / "notify" / "stalled.json"
        self._reg = reg
        self._panes = panes
        self._runtime = runtime
        self._push = push
        from . import feed_check
        self._bd_in_progress = bd_in_progress or (
            lambda: feed_check.bd_in_progress(feed_check.bd_cwd(reg), root=root, reg=reg))
        env = os.environ.get("SHANTY_STALL_MIN")
        self._threshold_s = (threshold_min if threshold_min is not None
                             else float(env) if env else 15.0) * 60.0
        # After the self-heal nudge (aegis-es1tt), wait this long STILL-frozen
        # before escalating to the coordinator. Default = one more threshold
        # window: the agent gets the same grace to act on the nudge that it got
        # to be noticed at all. SHANTY_STALL_ESCALATE_MIN overrides.
        eenv = os.environ.get("SHANTY_STALL_ESCALATE_MIN")
        self._escalate_after_s = (
            escalate_after_min * 60.0 if escalate_after_min is not None
            else float(eenv) * 60.0 if eenv else self._threshold_s)
        self._now = now or time.time
        self._log = log or (lambda msg: None)

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, store: dict) -> None:
        from .files import write_json_atomic
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, store)

    # aegis-es1tt: an anchor waiting on an owner/human DECISION is not a neglected
    # one — it is correctly parked, and telling its holder to "close it" is the
    # wrong action (the weaver/0lq5 care note). These label prefixes mark that
    # class (decision-stiwi, blocked, blocked-on-human, awaiting-…). Matched on
    # the labels bd already returns with each in_progress item, so it costs nothing.
    _BLOCKED_LABEL_PREFIXES = ("decision", "blocked", "awaiting", "needs-decision")

    @classmethod
    def _blocked_on_decision(cls, item: dict) -> bool:
        return any(
            any(str(l).lower().startswith(p) for p in cls._BLOCKED_LABEL_PREFIXES)
            for l in (item.get("labels") or []))

    def sweep(self, agents) -> dict:
        """REMEDIATE a neglected anchor, do not just report it (aegis-es1tt).

        e01l detected the stall and told the coordinator to go nudge it. This
        drives the remediation itself, in two stages per unchanged episode:

          1. threshold reached, still frozen -> NUDGE THE AGENT holding the
             anchor to close-or-release it (self-heal). The nudge names the exit
             both ways: `bd close <id>` if done, or note-why + `bd update <id>
             -a ""` to release if blocked/staged (the aegis-tgvtg affordance).
          2. escalate_after later, STILL frozen (the nudge did not land) -> push
             the COORDINATOR, the original e01l alert.

        Any progress — pane text, held set, a live shell, a non-idle pane —
        resets the episode, so an agent that acts on the nudge is never
        escalated. Each stage fires ONCE per episode.

        CARE (aegis-es1tt): an anchor carrying a decision/blocked label (e.g.
        decision-stiwi) is EXCLUDED — it is waiting on an owner answer already
        requested, and 'close it' is the wrong instruction. A worker holding
        ONLY such anchors is left entirely alone.

        The nudge rides `panes.send` — the SAME journaled tmux path the
        coordinator pushes use, NOT a raw cron write (aegis-tdesp: the retired
        cron's unjournaled sends manufactured the apz9 injection signature).

        Returns {"nudged": [names], "escalated": [names]}. FAIL OPEN: any error
        touches nothing.
        """
        import hashlib
        from .runtime import asks_a_question, auth_expired

        try:
            held: dict[str, list[str]] = {}
            for b in self._bd_in_progress():
                w = (b.get("assignee") or "").split("/")[-1]
                if w and not self._blocked_on_decision(b):
                    held.setdefault(w, []).append(b.get("id", "?"))
        except Exception:
            return {"nudged": [], "escalated": []}   # bd hiccup -> fail open
        store = self._load()
        now = self._now()
        nudged, escalated = [], []
        try:
            for ag in agents:
                if ag.role != "worker" or not ag.pane:
                    continue
                items = sorted(held.get(ag.name, []))
                if not items or not self._panes.exists(ag.pane):
                    store.pop(ag.name, None)   # nothing NEGLECTED held -> not ours
                    continue
                screen = self._panes.capture(ag.pane, attrs=True)
                plain = triage_mod.strip_attrs(screen)
                state = triage_mod.work_state(
                    screen, self._runtime.shows_ready_ui(plain),
                    awaiting=asks_a_question(self._runtime, plain),
                    auth_dead=auth_expired(self._runtime, plain))
                shells = triage_mod.running_shells(plain)
                if state != triage_mod.IDLE or (shells is not None and shells > 0):
                    store.pop(ag.name, None)   # busy/waiting/auth-dead, or a live
                    continue                   # shell: that is progress, re-arm
                key = hashlib.sha256(
                    (plain + "\n" + "|".join(items)).encode()).hexdigest()
                ep = store.get(ag.name)
                if not ep or ep.get("key") != key:
                    store[ag.name] = {"key": key, "since": now, "stage": None}
                    continue
                stage = ep.get("stage")
                mins = (now - ep["since"]) / 60
                if stage is None and now - ep["since"] >= self._threshold_s:
                    # STAGE 1: self-heal nudge to the agent's OWN pane.
                    if self._deliver_agent(ag, self._nudge_message(items, mins)):
                        ep["stage"] = "nudged"
                        ep["nudged_at"] = now
                        nudged.append(ag.name)
                    else:
                        self._log(f"stalled: {ag.name} pane unreachable for the "
                                  f"self-heal nudge — not advanced, will retry")
                elif stage == "nudged" and (
                        now - ep.get("nudged_at", ep["since"])
                        >= self._escalate_after_s):
                    # STAGE 2: the nudge did not land -> escalate to coordinator.
                    admin = self._push(
                        self._reg, self._panes,
                        self._escalation_message(ag.name, items, mins))
                    if admin is not None:
                        ep["stage"] = "escalated"
                        escalated.append(ag.name)
                    else:
                        self._log(f"stalled: coordinator unreachable to escalate "
                                  f"{ag.name} — not advanced, will retry")
        except Exception:
            return {"nudged": [], "escalated": []}   # tmux/registry -> fail open
        self._save(store)
        return {"nudged": nudged, "escalated": escalated}

    def _deliver_agent(self, ag, message) -> bool:
        """Send the self-heal nudge to the agent's own pane over the journaled
        tmux path (aegis-tdesp). Returns False if the pane is gone, so an
        undelivered nudge does not burn the episode's one attempt."""
        try:
            if not ag.pane or not self._panes.exists(ag.pane):
                return False
            self._panes.send(ag.pane, attribute(message, ST_TEND))  # aegis-5vxmz
            return True
        except Exception:
            return False

    @staticmethod
    def _nudge_message(items, mins) -> str:
        one = items[0]
        return (f"⚠ st tend (self-heal) — you are idle holding {', '.join(items)} "
                f"with no progress for {mins:.0f}m. An in_progress bead DAMS your "
                f"haul until you resolve it. If it is DONE: `bd close {one}`. If "
                f"it is blocked or gated (nobody should work it yet): put why on "
                f"the bead, then `st defer {one} "
                f"<bead|human|access|external|parked> --reason-file <file>` — "
                f"that records the blocker kind and takes it OUT of the ready "
                f"pool (clearing the assignee alone only re-pools it for the next "
                f"idle agent). If you are mid-something this loop cannot see, "
                f"ignore this — any activity clears it.")

    @staticmethod
    def _escalation_message(name, items, mins) -> str:
        return (f"⚠ st tend — NEGLECTED anchor unresolved: {name} has held "
                f"{', '.join(items)} idle with no progress for {mins:.0f}m and "
                f"did NOT act on a self-heal nudge. The haul is dammed. Reclaim "
                f"the item or close it on the agent's behalf — a held bead nobody "
                f"is working is invisible starvation.")


# --- BLOCKED beads go silent forever (internal-ref) --------------------------

# Do not shout about a bead that was blocked this morning: a human may simply not
# have got to it. The alarm is for FORGOTTEN, not for pending.
BLOCKED_MIN_AGE_DAYS = 3
# ...and once it IS forgotten, re-surface on a cadence rather than every 5-minute
# pass. A daily nudge is a nudge; a nudge every pass is noise, and noise about
# forgotten work is how work stays forgotten.
BLOCKED_RENOTIFY_DAYS = 1
# Per-pass cap. The first run faces the whole accumulated backlog — 16 on the live
# store — and sixteen pushes into one pane is a wall, and a wall gets dismissed as
# noise. Capped, the backlog drains over passes instead of arriving as one blob.
# NEVER SILENTLY: the remainder is COUNTED in the last message of the pass, because
# a cap nobody is told about reads as "that was all of them", which is the same
# class of lie this whole feature exists to end.
BLOCKED_MAX_PER_PASS = 5


def _bead_age_days(row: dict, now: float | None = None) -> float | None:
    """Age from `created_at`, in days. None when unreadable.

    CREATED, NOT UPDATED, AND THE CHOICE IS MEASURED. bd exposes no
    status-transition timestamp — there is no `blocked_at` — so age must come
    from what exists, and `updated_at` is actively wrong:

        the 17-day P1 security bead read updated_at age = 0 DAYS,
        because a roster cut touched it that morning.

    Any comment, label or rehome resets `updated_at`. A re-surfacer built on it
    is silenced by its own fleet's housekeeping, which is exactly how that bead
    stayed quiet for seventeen days — every touch made it look fresh.

    `created_at` OVER-reports: a bead worked for weeks before it blocked looks
    older than its block. That is the correct direction for an alarm about
    forgotten work, and it is the same asymmetry argument the staleness metric
    already makes. Callers must render it as an upper bound — "created Nd ago",
    never "blocked for Nd".
    """
    raw = (row or {}).get("created_at")
    if not raw:
        return None
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        now = now if now is not None else datetime.now(timezone.utc).timestamp()
        return max(0.0, (now - t.timestamp()) / 86400.0)
    except (ValueError, TypeError):
        return None


def _blocked_kind(detail: dict) -> tuple[str, list[str]]:
    """Classify a blocked bead from the status of its issue blockers.

    The binary is operational, not ontological: an OPEN `blocks` dependency is
    work the tracker can watch; with no open issue blocker, only a human can
    clear/correct the blocked status. Closed dependencies deliberately count as
    no blocker — dependency_count was refuted because it includes them.
    """
    deps = [d for d in (detail.get("dependencies") or [])
            if d.get("dependency_type") == "blocks"]
    open_ids = [d.get("id", "?") for d in deps
                if (d.get("status") or "").lower() != "closed"]
    return ("work", open_ids) if open_ids else ("human", [])


class BlockedStaleAlerter:
    """RE-SURFACE beads that have been BLOCKED long enough to be forgotten.

    It classifies from dependency STATUS, not the two refuted proxies:

      * a decision LABEL — 0 of 16 blocked beads carry one, INCLUDING the
        seventeen-day P1 security specimen. A label-gated alerter is inert.
      * `dependency_count` — it counts CLOSED dependencies. One blocked bead's
        only dependency had closed weeks earlier; nothing held it, and it would
        have been classified "blocked on work" and stayed silent.

    A bead with at least one open `blocks` dependency is BLOCKED-ON-WORK. With
    no open issue blocker it is BLOCKED-ON-HUMAN: only a person can clear or
    correct the status. This includes the sharp all-dependencies-closed case;
    dependency count must never silence it.

    The gap this closes: a bead blocked on a human is not waiting, it is
    STOPPED. Nothing in this system asked a person a second time, so
    "blocked on <human>" was operationally identical to "abandoned" — while the
    bead's own status made it look handled. Seventeen days on a P1 security bead
    is the specimen.

    Deliberately NOT tied to plates or agents. The previous fix took blocked
    beads OFF plates, which was right and which also removed the last thing that
    touched them at all. Visibility has to come from somewhere that looks at the
    STORE, not at who happens to hold the bead.

    FAIL OPEN, like every alerter here: any error (bd, registry, tmux) pushes
    nothing and returns []. A broken re-surfacer must never break a tend pass.
    """

    def __init__(self, root, reg, panes, *, push=push_to_admin,
                 bd_blocked=None, bd_show=None, now=None, log=None):
        self._root = root          # aegis-mxgzh: the sweeps need it to resolve the br backend
        self.path = Path(root) / "notify" / "blocked_stale.json"
        self._reg = reg
        self._panes = panes
        self._push = push
        self._bd_blocked = bd_blocked
        self._bd_show = bd_show
        self._now = now
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

    def sweep(self) -> list[str]:
        """One pass. Returns the bead ids actually re-surfaced (usually none)."""
        from .inbox import is_blocked, is_decision
        from .feed_check import bd_cwd, bd_show
        cwd = bd_cwd(self._reg) if self._bd_blocked is None else None
        try:
            if self._bd_blocked is not None:
                rows = self._bd_blocked()
            else:
                from .feed_check import bd_blocked
                rows = bd_blocked(bd_cwd(self._reg), root=self._root, reg=self._reg)
        except Exception as e:                      # FAIL OPEN
            self._log(f"blocked-stale: could not read the store ({e!r})")
            return []
        if self._bd_show is None and cwd is None:
            try:
                cwd = bd_cwd(self._reg)
            except Exception as e:
                self._log(f"blocked-stale: could not resolve the store ({e!r})")
                return []

        now = self._now() if callable(self._now) else self._now
        if now is None:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).timestamp()

        ledger = self._load()
        live = set()
        due = []
        for r in rows or []:
            bid = r.get("id")
            if not bid or not is_blocked(r.get("status")):
                continue
            live.add(bid)
            age = _bead_age_days(r, now)
            if age is None or age < BLOCKED_MIN_AGE_DAYS:
                continue
            last = ledger.get(bid)
            decision_hold = is_decision(r.get("labels"))
            verified_at = r.get("updated_at") or r.get("created_at") or "unknown"
            if decision_hold:
                # A decision label is a standing acknowledgement, not another
                # forgotten block. Report it once per bead revision; repeating
                # the same escalation every day only forces the coordinator to
                # re-read the same decision history (aegis-y4so3).
                if (isinstance(last, dict)
                        and last.get("kind") == "decision-hold"
                        and last.get("verified_at") == verified_at):
                    continue
            elif (isinstance(last, (int, float))
                  and (now - last) < BLOCKED_RENOTIFY_DAYS * 86400):
                continue                            # already nudged this cadence
            try:
                detail = self._bd_show(bid) if self._bd_show else bd_show(cwd, bid, root=self._root, reg=self._reg)
            except Exception as e:
                self._log(f"blocked-stale: could not inspect {bid} ({e!r})")
                continue
            kind, blockers = _blocked_kind(detail)
            due.append((bid, r, age, kind, blockers))

        # Re-arm: a bead that is no longer blocked-on-human is forgotten, so if it
        # returns to that state it alerts again rather than staying suppressed by
        # a stale entry.
        for bid in [b for b in ledger if b not in live]:
            del ledger[bid]

        surfaced = []
        # PRIORITY FIRST, THEN AGE — and this was WRONG on the first deploy, caught
        # only by watching a real pass. Sorted on age alone, the live store put
        # four 36-40d P2s ahead of the 17-day P1 SECURITY bead this whole feature
        # exists for; it came SIXTH and was held back by the per-pass cap. An
        # alarm whose ordering buries its own headline case is not an alarm.
        # Missing/unparseable priority sorts LAST rather than first: an unknown
        # is not an emergency, and treating it as one would let a malformed row
        # displace a real P1.
        def _rank(item):
            _bid, row, age, _kind, _blockers = item
            try:
                prio = int(row.get("priority"))
            except (TypeError, ValueError):
                prio = 99
            return (prio, -age)
        due.sort(key=_rank)
        held_back = max(0, len(due) - BLOCKED_MAX_PER_PASS)
        for i, (bid, r, age, kind, blockers) in enumerate(due[:BLOCKED_MAX_PER_PASS]):
            who = (r.get("assignee") or "").split("/")[-1] or "nobody"
            decision_hold = is_decision(r.get("labels"))
            verified_at = r.get("updated_at") or r.get("created_at") or "unknown"
            if decision_hold:
                classification = ("DELIBERATE HOLD: decision label present; "
                                  f"verified at {verified_at}. This standing "
                                  "notice repeats only when the bead changes.")
            elif kind == "work":
                classification = (f"BLOCKED-ON-WORK: open issue blocker(s) "
                                  f"{', '.join(blockers)}. Chase that work; do "
                                  f"not re-ask a human for the blocked bead.")
            else:
                classification = ("BLOCKED-ON-HUMAN: no open issue blocker "
                                  "exists. A person must clear/correct the status "
                                  "or supply the external decision/action.")
            heading = ("BLOCKED deliberate hold" if decision_hold
                       else "⚠ BLOCKED and going stale")
            msg = (f"{heading}: {bid} "
                   f"(p{r.get('priority')}, assignee {who}) — created "
                   f"{age:.0f}d ago, still blocked. {(r.get('title') or '')[:90]}. "
                   f"{classification} "
                   f"BLOCKED is invisible everywhere else — off bd ready, off the "
                   f"Rule Zero sweep, off every capacity report — so it is STOPPED, "
                   f"not waiting. "
                   f"(age is created_at, an UPPER bound — bd records no "
                   f"blocked-at timestamp.)")
            if held_back and i == min(BLOCKED_MAX_PER_PASS, len(due)) - 1:
                msg += (f" [+{held_back} more aged blocked bead(s) held back this "
                        f"pass to avoid a wall; they surface on following passes.]")
            if self._push(self._reg, self._panes, msg):
                ledger[bid] = ({"kind": "decision-hold", "verified_at": verified_at}
                               if decision_hold else now)
                surfaced.append(bid)
            else:
                self._log(f"blocked-stale: {bid} is stale but the push to the "
                          f"administrator did not land — NOT ledgered, so it "
                          f"retries next pass")

        self._save(ledger)
        return surfaced


class BlockedMisstatusAlerter:
    """Report BLOCKED beads whose issue blockers are all CLOSED.

    This is intentionally separate from BlockedStaleAlerter. Age means "chase
    the still-real blocker"; this condition means "clear the stale status" and
    is actionable immediately. Combining their messages would erase that
    distinction and make a freshly mis-statused P1 wait three days for an age
    threshold even though no dependency holds it.

    A blocked bead with NO issue dependencies is not classified. Its reason may
    live in prose or in an external dependency the issue view cannot enumerate;
    treating absence as all-closed is the false-clear direction.
    """

    def __init__(self, root, reg, panes, *, push=push_to_admin,
                 bd_blocked=None, bd_show=None, now=None, log=None):
        self._root = root          # aegis-mxgzh: the sweeps need it to resolve the br backend
        self.path = Path(root) / "notify" / "blocked_misstatus.json"
        self._reg = reg
        self._panes = panes
        self._push = push
        self._bd_blocked = bd_blocked
        self._bd_show = bd_show
        self._now = now
        self._log = log or (lambda msg: None)

    def sweep(self) -> list[str]:
        from .feed_check import bd_blocked, bd_cwd, bd_show
        from .files import write_json_atomic
        from .inbox import is_blocked
        # A fully injected reader is hermetic and has no registry/cwd at all in
        # tests. Resolve the production cwd only when a production reader needs
        # it; otherwise a diagnostic dependency outside the data path can make
        # a complete fixture look unreadable.
        cwd = (bd_cwd(self._reg)
               if self._bd_blocked is None or self._bd_show is None else None)
        try:
            rows = self._bd_blocked() if self._bd_blocked else bd_blocked(cwd, root=self._root, reg=self._reg)
        except Exception as e:
            self._log(f"blocked-misstatus: could not read blocked beads ({e!r})")
            return []

        try:
            ledger = json.loads(self.path.read_text())
        except (OSError, ValueError):
            ledger = {}
        now = self._now() if callable(self._now) else self._now
        if now is None:
            import time
            now = time.time()

        live, due = set(), []
        for row in rows or []:
            bid = row.get("id")
            if not bid or not is_blocked(row.get("status")):
                continue
            try:
                detail = self._bd_show(bid) if self._bd_show else bd_show(cwd, bid, root=self._root, reg=self._reg)
            except Exception as e:
                self._log(f"blocked-misstatus: could not inspect {bid} ({e!r})")
                continue
            deps = [d for d in (detail.get("dependencies") or [])
                    if d.get("dependency_type") == "blocks"]
            # Non-empty is load-bearing: all([]) is True in Python and exactly
            # wrong operationally for prose/external blocks.
            if not deps or not all((d.get("status") or "") == "closed" for d in deps):
                continue
            live.add(bid)
            last = ledger.get(bid)
            if not isinstance(last, (int, float)) or now - last >= 86400:
                due.append((bid, row, [d.get("id", "?") for d in deps]))

        for bid in [b for b in ledger if b not in live]:
            del ledger[bid]
        surfaced = []
        for bid, row, deps in due:
            msg = (f"⚠ MIS-STATUSED BLOCKED bead: {bid} has dependencies and "
                   f"EVERY one is closed ({', '.join(deps)}). It is unblocked "
                   f"in fact but blocked on paper, so bd ready and every feed "
                   f"path hide it. Clear/correct the status; do not chase a "
                   f"blocker that no longer exists. {(row.get('title') or '')[:90]}")
            if self._push(self._reg, self._panes, msg):
                ledger[bid] = now
                surfaced.append(bid)
            else:
                self._log(f"blocked-misstatus: {bid} push did not land — not ledgered")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, ledger)
        return surfaced
