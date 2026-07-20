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
from .runtime import asks_a_question
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
            awaiting=asks_a_question(runtime, plain))
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(ledger, indent=2, sort_keys=True))

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
