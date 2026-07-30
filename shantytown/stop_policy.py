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
  5  otherwise                                      -> ALLOW

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
    hibernate: "config.Hibernate | None" = None
    minutes_quiet: float | None = None
    fleet: "config.Fleet | None" = None
    load_per_core: float | None = None      # None = could not measure

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
            return Verdict(False,
                           f"nothing dispatchable, nothing urgent. "
                           f"{len(inp.pending)} event(s) left PENDING, unconsumed. "
                           f"Next wake: a tend push, an inbox, a dispatch{left}.",
                           BY_HIBERNATE)

    if inp.pending:
        return Verdict(True, f"{len(inp.pending)} pending event(s) to deliver.",
                       BY_EVENTS)
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

    inp = Inputs(me=me, role=role, pending=list(events.pending(me)))

    if role != "administrator":
        # Rule Zero and hibernate are the coordinator's. A lead's drain is how it
        # ABSORBS its reports; sleeping it would hold back the absorbing half of
        # the tier while the delegating half kept running.
        return inp

    try:
        free, ready = feed_mod.gate_inputs(root, reg, panes, runtime, me)
        inp.free_feedable, inp.dispatchable = free, ready
    except Exception:      # noqa: BLE001 — fail open: no gate, no block
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
