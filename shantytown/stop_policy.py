"""stop_policy — ONE stop decision. `python -m shantytown.stop_policy --root <dir>`.

Built to docs/stop-policy-spec.md. An agent's Stop hook was a CHAIN of independent
commands, each able to return {"decision":"block"} on its own and none of them
aware of the others. Measured consequences: a documented config knob (hibernate)
that was inert because a second hook blocked the same stop; the same panes scraped
up to three times in one turn boundary; and two policies whose overlap nobody had
had to reconcile because nothing forced them into one answer.

THE RANKS. Ordered, first match wins. This list IS the specification — a reader
must be able to answer "why did my coordinator not stop?" from it alone.

  0  route + persist MY OWN stop event upward      (side effect, never blocks)
  1  a pending event is URGENT (governance / rose)  -> BLOCK: deliver
  2  RULE ZERO: free feedable AND dispatchable      -> BLOCK: dispatch
  3  hibernate declines                             -> ALLOW, loudly
  4  a DELIVERABLE pending event                    -> BLOCK: deliver
  5  idle because a GOVERNOR TIER holds the queue   -> ALLOW, loudly
  6  otherwise                                      -> ALLOW

RANK 5 IS AN ALLOW THAT REFUSES TO BE SILENT (aegis-diasw). Under an engaged
usage tier, an idle fleet with a full ready queue is the CORRECT state — and it
is observationally identical to a feeder that has broken. A coordinator that
cannot tell them apart assumes the second, because that is the one it can act on;
and the action available to it is to re-grade a P2 to P1 until the queue clears
the floor. That is priority inflation manufactured by two mechanisms disagreeing,
and it defeats the throttle at exactly the moment the throttle is working.

The fix is to remove the PUSH, not the escape hatch: `st go`'s refusal still
offers the bump, because bd history records priority per revision so an inflation
is auditable after the fact (ruled by Stiwi, who withdrew the decision that would
have reworded it). What this rank removes is the blocking hook demanding an action
the governor forbids — nobody is herded toward the hatch, so a bump becomes a
judgement someone made rather than one the mechanism extracted.

RANK 2 ABOVE RANK 3 IS THE WHOLE FIX. Hibernate can now only fire in a state where
quiet is correct — nothing urgent, nothing to hand out — and when Rule Zero
overrides it, the output says so by name instead of the knob appearing broken.

RANK 3 ABOVE RANK 4 IS THE FEATURE, not a gap. A hibernating administrator sleeps
through ORDINARY worker reports: with nothing dispatchable, a "kelly stopped" is
informational and there is no decision to make. It is safe for exactly one reason —
rank 3 ALLOWS without consuming anything, so the next wake sees the whole batch.

FAIL OPEN, ranks 1-5. Any error — tmux, bd, registry, a config typo — allows the
stop. A hook that wedges an agent on a transient hiccup is worse than the stall it
prevents. Rank 0 is the exception: persisting my own stop event is survival, not a
decision, so a failure there is reported rather than swallowed into a clean-looking
allow.

NOT AN `st` SUBCOMMAND, for the same reason stop_event is not: this is hook
plumbing, and the command surface is pinned.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

from . import config
from . import feed_check as feed_mod
from . import stop_event
from . import triage
from .events import FilesEvents
from .files import FilesRegistry
from .hibernate import WakeLog
from .runtime import ClaudeRuntime
from .tier import is_governance
from .tmux import Tmux

# Which rank decided. Emitted on stderr, so "went quiet" and "is wedged" are
# distinguishable without reading this file.
BY_STOOD_DOWN = "stood-down"
BY_URGENT = "urgent"
BY_RULE_ZERO = "rule-zero"
BY_HIBERNATE = "hibernating"
BY_EVENTS = "events"
BY_THROTTLED = "governor-throttled"
BY_NOTHING = "nothing-to-do"
BY_ERROR = "fail-open"


@dataclass(frozen=True)
class Verdict:
    block: bool
    reason: str          # reaches the MODEL when blocking (the block protocol)
    by: str              # which rank decided; stderr only


@dataclass
class Inputs:
    """Everything the ranks read, gathered ONCE.

    The single sweep is both properties at once: the ranks cannot disagree about
    who is busy (they are looking at the same instant), and one turn boundary
    costs one scrape instead of three.
    """
    me: str
    role: str
    pending: list = field(default_factory=list)      # NOT consumed — a read
    free_feedable: list = field(default_factory=list)
    dispatchable: int = 0
    # Ready, unassigned, claimable — and REFUSED BY THE GOVERNOR's priority floor
    # (aegis-diasw). Not folded into `dispatchable`: the whole defect was that
    # "throttle holding" and "feeder broken" were the same observation, and one
    # number cannot carry both. `throttled_why` is the governor's own sentence.
    throttled: int = 0
    throttled_why: str = ""
    hibernate: "config.Hibernate | None" = None
    minutes_quiet: float | None = None
    fleet: "config.Fleet | None" = None
    load_per_core: float | None = None      # None = could not measure
    # Senders observed mid-flight at THIS sweep. Empty = nothing known busy, so
    # everything is deliverable — the same fail-open `_drain` uses when it has no
    # pane backend: refusing to act on a check we did not run would be worse than
    # the bug. See `deliverable`.
    busy_senders: set = field(default_factory=set)

    @property
    def deliverable(self) -> list:
        """Pending events that would ACTUALLY be handed over on this stop.

        RANK 4 SAYS "a DELIVERABLE pending event" AND THE CODE READ `pending`
        (aegis-d1qko). `_drain` holds an event back while its SENDER is
        mid-flight, so on a busy fleet the coordinator blocked on events that the
        very next call then declined to deliver: ~10 no-op turns in one night
        against 2 held events. Blocking on a set larger than the deliverable one
        is a wake with nothing to do, every turn, for as long as the senders keep
        working — which on a healthy fleet is indefinitely.

        Urgent events are deliverable BY DEFINITION: `_drain` never defers a
        governance alert or a risen event, so the two filters must agree or a
        rank-1 block would hand over nothing."""
        import time as _t
        now = _t.time()
        out = []
        for e in self.pending:
            if is_governance(e.reason) or e.rose:
                out.append(e)                     # never deferred by _drain
                continue
            # getattr, not attribute access: an event that cannot tell us its
            # sender is treated as DELIVERABLE, never held. Fail-open is this
            # module's rule everywhere else (see the unknown-role branch in
            # gather) and it is the right direction here too — holding a stop
            # back on a field we could not read is the aegis-d1qko bug wearing a
            # different hat.
            frm = getattr(e, "frm", None)
            if frm is None or frm not in self.busy_senders:
                out.append(e)
                continue
            # Sender is busy — held, UNLESS it has beaten _drain's ceiling.
            # This must use the same bound _drain does, or the two disagree
            # again and we are back to blocking on undeliverable events.
            ts = getattr(e, "ts", 0)
            age = (now - ts) if ts else float("inf")
            if age > stop_event.DEFER_MAX_AGE_S:
                out.append(e)
        return out

    @property
    def urgent(self) -> list:
        """Events that must never be slept on. A governance alert's content is
        'an agent is working RIGHT NOW outside the tracker'; a risen event means
        the tier already failed once by the time the admin sees it."""
        return [e for e in self.pending if is_governance(e.reason) or e.rose]


def decide(inp: Inputs) -> Verdict:
    """THE decision. Pure: it takes measurements, never makes them.

    Separated from the gathering so the ordering — the part that has to be
    argued — is testable without a tmux, a clock, a registry or a bd.
    """
    if inp.urgent:
        kinds = ", ".join(sorted({e.reason or "escalation" for e in inp.urgent}))
        return Verdict(True, f"{len(inp.urgent)} event(s) that must not wait "
                             f"({kinds}).", BY_URGENT)

    # RULE ZERO'S TWO PRECONDITIONS (GitHub #29, #23). Both make the gate
    # satisfiable by RESTRAINT, which it was not: it could only ever demand more
    # dispatch, so a deliberate quiet period was indistinguishable from neglect.
    stood_down = bool(inp.fleet and inp.fleet.stood_down)
    over_capacity = False
    cap = inp.fleet.max_load_per_core if inp.fleet else 0.0
    if cap and inp.load_per_core is not None:
        over_capacity = inp.load_per_core > cap

    if inp.free_feedable and inp.dispatchable and stood_down:
        # The operator has DECLARED quiet. Rule Zero yields, and says so — a gate
        # that goes silent is indistinguishable from a gate that is broken.
        return Verdict(False,
                       f"the fleet is STOOD DOWN ({len(inp.free_feedable)} free "
                       f"worker(s) and {inp.dispatchable} ready bead(s) left "
                       f"alone). Clear `[fleet] stood_down` to resume dispatch.",
                       BY_STOOD_DOWN)

    if inp.free_feedable and inp.dispatchable and over_capacity:
        return Verdict(False,
                       f"host load {inp.load_per_core:.1f}/core is over the "
                       f"{cap:.1f} ceiling — NOT demanding more dispatch onto a "
                       f"saturated box. {inp.dispatchable} bead(s) wait.",
                       BY_STOOD_DOWN)

    if inp.free_feedable and inp.dispatchable:
        # RULE ZERO (aegis-hfta), and it OVERRIDES hibernate — but says so, which
        # is the difference between a policy and a knob that looks broken.
        note = ""
        if inp.hibernate is not None and inp.hibernate.enabled:
            note = (" Hibernate is enabled and was OVERRIDDEN: there is work to "
                    "hand out.")
        return Verdict(True,
                       f"{len(inp.free_feedable)} free feedable worker(s) "
                       f"({', '.join(sorted(inp.free_feedable))}) and "
                       f"{inp.dispatchable} dispatchable bead(s). Dispatch, or "
                       f"say why not.{note}", BY_RULE_ZERO)

    # NOTHING PENDING IS NOT A HIBERNATION. It is the ordinary idle stop (rank 5),
    # which allows anyway. Claiming rank 3 here would report holding a backlog back
    # when there was no backlog, and an operator reading that goes looking for
    # events that do not exist.
    if inp.pending and inp.hibernate is not None and inp.hibernate.enabled:
        if not _batch_is_stale(inp):
            left = ""
            if inp.hibernate.max_quiet_minutes and inp.minutes_quiet is not None:
                remaining = inp.hibernate.max_quiet_minutes - inp.minutes_quiet
                left = f", or {remaining:.0f} min of quiet remaining"
            # "nothing dispatchable" is TRUE but incomplete when a tier is the
            # reason (aegis-diasw): an operator reading it goes looking for an
            # empty queue and finds a full one. Name the throttle here too.
            why = ""
            if inp.throttled:
                why = (f" ({inp.throttled} ready bead(s) held by the usage "
                       f"governor's priority floor — idle is correct)")
            return Verdict(False,
                           f"nothing dispatchable{why}, nothing urgent. "
                           f"{len(inp.pending)} event(s) left PENDING, unconsumed. "
                           f"Next wake: a tend push, an inbox, a dispatch{left}.",
                           BY_HIBERNATE)

    if inp.deliverable:
        held = len(inp.pending) - len(inp.deliverable)
        # Name the held-back remainder IN the block line. Its absence is what
        # sent a coordinator to the events directory hunting a stuck-delivery bug
        # that was disclosed design all along (aegis-d1qko).
        note = (f" ({held} more held back — sender(s) still mid-flight)"
                if held else "")
        return Verdict(True,
                       f"{len(inp.deliverable)} pending event(s) to deliver."
                       f"{note}",
                       BY_EVENTS)

    # RANK 5. Nothing to dispatch — and the governor is WHY. Said out loud, on an
    # ALLOW, because silence here is what trains a coordinator to go hunting for
    # a way to make work dispatchable. Deliberately AFTER the event ranks: a
    # throttled queue is no reason to sit on someone's undelivered report.
    if inp.free_feedable and inp.throttled and not inp.dispatchable:
        return Verdict(False,
                       f"the usage governor is holding the queue: "
                       f"{len(inp.free_feedable)} idle worker(s) and "
                       f"{inp.throttled} ready bead(s), 0 clearing the priority "
                       f"floor. IDLE IS CORRECT — the throttle is holding, not "
                       f"the feeder. Nothing here needs doing. "
                       f"{inp.throttled_why}".rstrip(),
                       BY_THROTTLED)
    return Verdict(False, "", BY_NOTHING)


def _batch_is_stale(inp: Inputs) -> bool:
    """Has the pending batch gone unread longer than the operator allows?

    max_quiet_minutes is NOT a schedule to wake on — it is a bound on how long a
    pending batch may sit while nothing pushes. 0 disables it, which is a
    legitimate choice: `st tend` pushes, and a push is a wake with a REASON, which
    beats a timer every time.
    """
    cap = inp.hibernate.max_quiet_minutes if inp.hibernate else 0
    if not cap or not inp.pending:
        return False
    if inp.minutes_quiet is None:
        return True          # never woken -> read the batch once, start the clock
    return inp.minutes_quiet >= cap


# --- gathering --------------------------------------------------------------

def gather(root, me: str, *, reg=None, panes=None, runtime=None,
           events=None, wake_log=None, now=None) -> Inputs:
    """One sweep. Every dependency injectable so a test drives the ranks without
    tmux or bd."""
    import time
    reg = reg if reg is not None else FilesRegistry(root / "crew")
    panes = panes if panes is not None else Tmux()
    runtime = runtime if runtime is not None else ClaudeRuntime(
        panes, lambda card: None, root=root)
    events = events if events is not None else FilesEvents(root / "events")

    role = "worker"
    try:
        role = reg.get(me).role
    except Exception as e:  # noqa: BLE001 — unknown identity: the safe role
        # LOUD. Degrading to `worker` disables BOTH Rule Zero and hibernate, so a
        # silent fall-through here makes a coordinator quietly stop coordinating —
        # the same shape as the failures this whole module is consolidating. Fail
        # open, but never fail open quietly.
        print(f"stop_policy: could not read my own card ({e!r}) — treating "
              f"{me!r} as a worker, so Rule Zero and hibernate are OFF this stop",
              file=sys.stderr)

    pending = list(events.pending(me))
    # WHO IS MID-FLIGHT — measured once, here, in the same sweep as every other
    # rank, so the block decision and the drain that follows it cannot disagree
    # about who is busy. A failure to measure leaves the sender OUT of the set,
    # i.e. treated as deliverable: same fail-open as `_drain` without a pane
    # backend. `_liveness` is imported rather than reimplemented on purpose —
    # two copies of this predicate is how rank 4 and the drain drifted apart.
    busy: set = set()
    for name in {e.frm for e in pending}:
        try:
            if stop_event._liveness(reg, panes, runtime.shows_ready_ui,
                                    name) == triage.BUSY:
                busy.add(name)
        except Exception as e:  # noqa: BLE001 — unreadable pane is not busy
            print(f"stop_policy: could not read liveness for {name!r} ({e!r}) "
                  f"— treating as deliverable", file=sys.stderr)
    inp = Inputs(me=me, role=role, pending=pending, busy_senders=busy)

    if role != "administrator":
        # Rule Zero and hibernate are the coordinator's. A lead's drain is how it
        # ABSORBS its reports; sleeping it would hold back the absorbing half of
        # the tier while the delegating half kept running.
        return inp

    try:
        free, ready, held = feed_mod.gate_inputs(root, reg, panes, runtime, me)
        inp.free_feedable, inp.dispatchable = free, ready
        inp.throttled = len(held)
        inp.throttled_why = held[0][2] if held else ""
    except Exception as exc:      # noqa: BLE001 — fail open: no gate, no block
        print(f"stop_policy: Rule Zero feed-path read FAILED ({exc!r}) — "
              "ALLOWING this stop because the gate cannot prove work is dispatchable",
              file=sys.stderr)
        inp.free_feedable, inp.dispatchable = [], 0

    cfg, err = config.load_or_default(root)
    if err:
        print(f"stop_policy: {err} — running with hibernate OFF", file=sys.stderr)
    inp.fleet = cfg.fleet
    if cfg.fleet.max_load_per_core:
        inp.load_per_core = _load_per_core()
    if cfg.hibernate.enabled:
        inp.hibernate = cfg.hibernate
        log = wake_log if wake_log is not None else WakeLog(root)
        inp.minutes_quiet = log.minutes_since(me, now or time.time())
    return inp


def _load_per_core() -> float | None:
    """1-minute load average per CPU. None when it cannot be read — and None means
    the capacity check does not fire, because refusing to dispatch on a number we
    could not measure would be the mirror of the bug (#23)."""
    try:
        import os as _os
        cores = _os.cpu_count() or 1
        return _os.getloadavg()[0] / cores
    except Exception:      # noqa: BLE001 — no /proc/loadavg, no answer
        return None


def run(root, me: str, **kw) -> int:
    """Gather, decide, emit. Returns the process exit code (always 0 — a stop hook
    that exits non-zero is a stop hook that breaks the agent)."""
    import time
    try:
        inp = gather(root, me, **kw)
        verdict = decide(inp)
    except Exception as e:  # noqa: BLE001 — FAIL OPEN, see the module docstring
        print(f"stop_policy: could not decide ({e!r}) — ALLOWING the stop",
              file=sys.stderr)
        return 0

    print(f"stop_policy: {'BLOCK' if verdict.block else 'ALLOW'} "
          f"({verdict.by}) — {verdict.reason}".rstrip(" —"), file=sys.stderr)

    if not verdict.block:
        return 0

    # DELIVERY. The events half runs through stop_event's existing drain, which
    # owns BLOCK-ONCE, the mid-flight deferral, and the admin workflow
    # enrichment — none of which is reimplemented here. A rank that blocks for a
    # non-event reason (Rule Zero) still drains first, because the coordinator
    # being woken should see everything waiting for it in the same turn.
    payload = _drain_payload(root, me, kw)
    reason = verdict.reason if not payload else f"{verdict.reason}\n\n{payload}"
    print(json.dumps({"decision": "block", "reason": reason}))
    if (log := kw.get("wake_log")) is not None or True:
        try:
            (log or WakeLog(root)).record_wake(me, kw.get("now") or time.time())
        except Exception:  # noqa: BLE001 — a ledger must never fail a wake
            pass
    return 0


def _drain_payload(root, me: str, kw) -> str:
    """The drained events, rendered — or '' if there was nothing (or it failed).

    Captured rather than printed: stop_event._drain emits its own block payload on
    stdout, and TWO payloads in one hook is not a protocol. So this calls it with
    stdout redirected and lifts the reason out.
    """
    import contextlib
    import io
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            stop_event.main(["drain", "--root", str(root)])
    except Exception:      # noqa: BLE001 — a failed drain must not lose the block
        return ""
    out = buf.getvalue().strip()
    if not out:
        return ""
    try:
        return json.loads(out).get("reason", "")
    except ValueError:
        return ""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    me = os.environ.get("SHANTY_AGENT")
    if not me:
        print("stop_policy: $SHANTY_AGENT is unset — cannot resolve identity",
              file=sys.stderr)
        return 1
    root = stop_event._root(argv)
    # RANK 0, before any verdict: route and persist MY OWN stop event upward.
    # Survival, not a decision — a verdict must not be able to lose the event.
    try:
        reg = FilesRegistry(root / "crew")
        if reg.get(me).role != "administrator":
            stop_event.main(["send", "--root", str(root)])
    except Exception as e:  # noqa: BLE001 — reported, never swallowed
        print(f"stop_policy: could not persist my own stop event ({e!r})",
              file=sys.stderr)
    return run(root, me)


if __name__ == "__main__":
    raise SystemExit(main())
