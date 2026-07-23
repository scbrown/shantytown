"""stop_event — the hook entry. `python -m shantytown.stop_event send|drain`.

NOT an `st` subcommand (arnold's #6 ruling): `st stop` is taken and the twelve-
command surface is pinned + tested. This is PLUMBING the emitted Stop hook calls,
so the command-count test never sees it. Identity comes from $SHANTY_AGENT, which
the launcher (Runtime.start) already exports — the same identity `st prime` reads.

TWO MODES, the two halves of arnold's frame:

  send  — a non-root role, at ITS OWN stop, routes and PERSISTS its stop-event.
          route_stop(me) -> Routing(to, rose, reason); persist it. SURVIVAL: on
          the store before anyone reads it. Non-blocking, silent, exit 0. A worker
          is send-only; a lead also sends its own stop up to the admin.

  drain — a DESTINATION (lead/admin), at its own stop, DELIVERS: drain MY events
          and inject them into MY model via Claude Code's Stop-hook block protocol
          ({"decision":"block","reason":...}). reason reaches the MODEL;
          systemMessage would reach only the user's terminal, so it is never used
          here (arnold's rail 2). drain is BLOCK-ONCE (the store marks delivered),
          so a later stop with nothing new prints nothing and the destination
          idles instead of wedging.

A STOP EVENT IS A TURN BOUNDARY, NOT AN IDLE AGENT (internal-ref). Claude Code's
Stop hook fires at the end of every TURN. So `send` cannot know whether the agent
it names is finished or merely between thoughts, and it must not pretend: the
only pane it could inspect is its own, from inside its own blocking hook, and any
verdict it stamped would be stale before anyone read it. So the two halves split
the question by WHO CAN ANSWER IT:

  send  records only what is true at emit — ts, and the item it held (with its
        status), so the destination need not go re-read the tracker per agent.
  drain answers "is this agent free RIGHT NOW" itself, against a live pane, at the
        moment the decision is made. An agent still mid-flight is DEFERRED (not
        delivered, not marked) — so a turn boundary no longer wakes the root of
        the tier, and the event is still waiting when that agent really does stop.

The measurement that forced this: sattler was handed "tim stopped / kelly stopped
/ kelly stopped", opened the panes, and found both agents working (kelly's two
events were one continuous stretch of work). Trusting the event name would have
re-dispatched over live agents; distrusting it made the event worthless, since
the safe read was to scrape every pane by hand. drain now does that scrape.
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

from . import triage
from . import workflow
from .events import FilesEvents, StopEvent
from .files import FilesRegistry, FilesTracker, plate as files_plate
from .policy import NullRanker, PolicyRanker
from .protocols import RankUnavailable
from .runtime import ClaudeRuntime, live_wiring
from .tier import route_stop
from .triage import running_shells, context_tokens_k, CYCLE_THRESHOLD_K
from .tmux import Tmux


def _root(argv: list[str]) -> Path:
    # --root <dir>, else $SHANTY_ROOT, else cwd/.shanty. The CLI now resolves it
    # the same way (cli._default_root); it did not when this comment was
    # written, and the comment asserting agreement is what kept the
    # disagreement invisible.
    if "--root" in argv:
        return Path(argv[argv.index("--root") + 1])
    env = os.environ.get("SHANTY_ROOT")
    return Path(env) if env else Path.cwd() / ".shanty"


def _lead_is_up(reg: FilesRegistry, panes) -> "callable":
    """route_stop asks 'is this lead reachable?' — and REACHABLE MEANS IT WILL
    DRAIN, not that something answers to its name (dearing, internal-ref).

    This used to be `pane exists`, and that is the same defect one layer over
    from the checker's: a pane is a name, and a name is not a capability. It was
    measured — dearing's pane was resurrected by a foreign launcher (gt-crew-up)
    with settings carrying no `stop_event` hook, so:

        lead_is_up(dearing) -> True     (the pane is right there)
        7 workers  -> to=dearing, rose=False, no rise to the administrator
        dearing    -> cannot drain. Every one of those events was write-only.

    Being restarted made routing WORSE, because it made the lead look AVAILABLE.
    A down lead at least rises (Q3); a live-but-deaf lead swallows silently, and
    that is the failure mode this whole file exists to prevent.

    So `up` now means: the pane exists AND the process in it actually carries the
    `drain` direction. A lead that cannot drain is treated exactly like a lead
    that is down — the event RISES to the administrator, loudly, with a reason.
    That is strictly safer: the worst case is an event rising to the admin that a
    lead could have taken, which is noisy. The old worst case was silence.

    CANNOT-TELL FAILS TOWARD RISING on purpose. If we cannot read the process we
    do not know it will drain, and "assume it drains" is the assumption that lost
    the events.
    """
    def up(name: str) -> bool:
        try:
            lead = reg.get(name)
        except LookupError:
            return False
        if not lead.pane or not panes.exists(lead.pane):
            return False
        wiring = live_wiring(lead.pane, panes.cmdline)
        return wiring is not None and "drain" in wiring.directions
    return up


def _my_shells(reg: FilesRegistry, panes, me: str) -> int | None:
    """Background shells I still own AT MY OWN STOP (internal-ref).

    Read off MY pane, whose address comes from MY card — the same route
    _lead_is_up uses, so the hook needs no new coupling and no new env var. Any
    failure to look returns None, which the event records as NOT REPORTED. It
    must never fall back to 0: a fabricated "no shells running" is precisely the
    claim this bead exists to stop the tier from making, and it would be made at
    the one moment the destination is deciding whether the work is done.
    """
    try:
        pane = reg.get(me).pane
        return running_shells(panes.capture(pane)) if pane else None
    except Exception:
        return None


def _my_context_k(reg: FilesRegistry, panes, me: str) -> float | None:
    """My context depth AT MY OWN STOP, in k tokens (internal-ref).

    Read off my own pane, the same route as _my_shells — the "/clear to save N
    tokens" footer the runtime prints. A destination told only "gennaro stopped"
    hands gennaro the next item; told "gennaro stopped past the 400k cycle
    threshold at 687k" it does not. None on any failure, and — like shells — never
    a fabricated 0: a stop taken mid-turn has no footer to read, and "not reported"
    is the truth there, not "context is fine".
    """
    try:
        pane = reg.get(me).pane
        return context_tokens_k(panes.capture(pane)) if pane else None
    except Exception:
        return None


def _plate_of(root: Path, me: str) -> tuple[str | None, str | None]:
    """What `me` held when it stopped: (item_id, status).

    Three distinct answers, and the third is why this returns a pair instead of an
    id: (None, None) = the plate was empty; (id, status) = it held that; and
    (None, "?") = THE TRACKER DID NOT ANSWER. A lookup that failed must not render
    as finished work — that is the whole internal-ref lesson, and it is one `except`
    away from happening here.

    Files backend only, deliberately: the emitted hook command carries no --backend
    (test_role_emit pins it), so a beads path here would be a branch nothing can
    reach. Never fatal — a stop event that cannot name an item is still worth far
    more than no stop event.
    """
    try:
        item = files_plate(FilesTracker(root / "items"), me)
    except Exception:
        return None, "?"
    return (item.id, item.status) if item else (None, None)


def _send(reg: FilesRegistry, events: FilesEvents, panes, me: str,
          root: Path | None = None) -> int:
    try:
        routing = route_stop(reg, me, lead_is_up=_lead_is_up(reg, panes))
    except LookupError as e:
        # nowhere for the stop to go (no lead AND no administrator). This is a
        # real misconfiguration, surfaced — not swallowed. Non-zero so it shows.
        print(f"stop_event send: {e}", file=sys.stderr)
        return 1
    reason = routing.reason.value if routing.reason else None
    shells = _my_shells(reg, panes, me)
    context_k = _my_context_k(reg, panes, me)
    item, item_status = _plate_of(root, me) if root is not None else (None, "?")
    ev = events.persist(to=routing.to, frm=me, reason=reason, rose=routing.rose,
                        shells=shells, item=item, item_status=item_status,
                        context_k=context_k)
    over = context_k is not None and context_k >= CYCLE_THRESHOLD_K
    # Silent on stdout (a non-blocking Stop hook's stdout is discarded anyway);
    # a terse stderr line is useful when a human runs it by hand.
    print(f"stop_event: {me} stopped -> persisted {ev.id} to {routing.to}"
          + (f" (ROSE: {reason})" if routing.rose else "")
          + (f" [{shells} shell(s) still running]" if shells else "")
          + (f" [SATURATED {int(context_k)}k]" if over else ""), file=sys.stderr)
    return 0


# --- the HAUL advance (the sequenced-worker self-feed) -----------------------

# The mid-haul HANDOFF line, in k tokens: 60% OF THE ~1M WINDOW (Stiwi's call,
# on the design bead). Deliberately NOT derived from triage's 400k
# CYCLE_THRESHOLD_K — the two lines answer different questions. 400k is the
# NEW-work dispatch wall: past it, an agent must cycle before TAKING work. A
# hauling worker is different: between beads its context is disposable BY
# CONSTRUCTION (the anchor just closed, the work is durable in the bead trail),
# so the haul may grind past 400k — and at 600k the advance stops feeding and
# instructs the handoff instead.
HAUL_HANDOFF_K = 600.0


def _bd_json(args: list[str], cwd: str | None) -> list[dict]:
    """One bd read, JSON out, or raise — the caller's fail-open catches it."""
    import subprocess
    r = subprocess.run(["bd", *args, "--json"], capture_output=True, text=True,
                       timeout=20, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"bd {' '.join(args)} failed: {r.stderr.strip()}")
    return json.loads(r.stdout)


def _assigned_to(me: str, beads: list[dict]) -> list[dict]:
    """The beads assigned to `me` — trailing-segment match, the same parse
    feed_check.hauls uses (bd stores crew paths or bare names)."""
    out = []
    for b in beads:
        assignee = b.get("assignee") or ""
        if assignee.split("/")[-1] == me:
            out.append(b)
    return out


def _haul(reg: FilesRegistry, panes, me: str, root: Path) -> int:
    """The worker's own advance: anchor closed + assigned ready work -> BLOCK
    the stop with the next bead as the reason — the same model-reaching
    protocol drain and the Rule Zero gate already use. The coordinator is not
    involved at any point; that is the feature.

    A STOP IS A TURN BOUNDARY, NOT AN IDLE AGENT (internal-ref), so the advance
    fires only on evidence the anchor actually finished: nothing of mine
    in_progress AND something of mine ready. Mid-work turn ends fall through
    silently — halting there would halt every haul within minutes (the design
    correction this module's own header taught).

    SELF-TERMINATING like feed_check: each feed claims the bead in_progress,
    so the next stop sees an active anchor and allows. The handoff branch
    blocks until the agent /clears — it terminates on the RIGHT condition
    (compliance), never a counter.

    FAIL-OPEN ABSOLUTELY: any error allows the stop, and the worker degrades
    to the tend self-feed nudge (the belt) and normal idle flow. A broken
    advance must never trap a worker at its own stop."""
    try:
        if reg.get(me).role != "worker":
            return 0
        from .feed_check import bd_cwd
        cwd = bd_cwd(reg)
        # An active anchor = mid-work turn boundary. bd list is filtered
        # client-side (same reason as feed_check: assignee formats vary).
        active = _assigned_to(me, _bd_json(["list", "--status", "in_progress"], cwd))
        if active:
            return 0
        mine = _assigned_to(me, _bd_json(["ready"], cwd))
        if not mine:
            return 0

        # THE HANDOFF LINE: past 60% of the window, the advance stops feeding
        # and instructs the reset — between beads is the uniquely safe moment
        # to shed context, and feeding another bead here would spend the
        # remaining headroom on work that deserves a fresh session. None
        # (footer unreadable) is NOT over the line — unknown never blocks.
        from .feed_check import haul_feed_message, haul_handoff_message
        ck = _my_context_k(reg, panes, me)
        if ck is not None and ck >= HAUL_HANDOFF_K:
            print(json.dumps({"decision": "block",
                              "reason": haul_handoff_message(ck, HAUL_HANDOFF_K)}))
            return 0

        nxt = mine[0]
        nid = nxt.get("id", "?")
        title = (nxt.get("title") or "")[:80]
        rest = len(mine) - 1
        # Claim it the way a dispatch would, so the tracker shows the truth and
        # the next stop sees an active anchor. Best-effort: a failed claim
        # still feeds — the agent claims by hand per the instruction. The
        # message is feed_check's — ONE voice for both advance triggers.
        try:
            _bd_json(["update", nid, "--status", "in_progress"], cwd)
        except Exception:
            pass
        print(json.dumps({"decision": "block",
                          "reason": "anchor closed ✓ — "
                          + haul_feed_message(nid, title, rest)}))
        return 0
    except Exception:
        return 0                     # fail-open: never trap a worker's stop


DOWN = "down"        # a fifth verdict triage cannot produce: there is no pane.


def _liveness(reg: FilesRegistry, panes, shows_ready_ui, name: str,
              awaiting_answer=None) -> str:
    """Is `name` working RIGHT NOW? The scrape sattler had to do by hand.

    Answered here, at read time, and never stored — a liveness verdict is only
    true at the instant it is taken, and this is that instant.

    `down` is separate from triage's four on purpose: a missing card or a dead
    pane is not `?` ("I looked and could not tell"), it is "there is nothing to
    look at", and it is a fact the coordinator must ACT on rather than wait out.
    Anything that is not BUSY gets delivered — an agent that is wedged, gone, or
    unreadable is exactly who a coordinator needs waking for.
    """
    try:
        card = reg.get(name)
    except Exception:
        return DOWN
    if not card.pane or not panes.exists(card.pane):
        return DOWN
    screen = panes.capture(card.pane)
    # awaiting_answer is optional so a caller with no runtime still gets a verdict
    # — one degraded to `?`, exactly as before, rather than a crash.
    awaiting = bool(awaiting_answer(screen)) if awaiting_answer else False
    return triage.work_state(screen, shows_ready_ui(screen), awaiting=awaiting)


def _age(ts: float, now: float) -> str:
    """How stale is this event? 'age unknown' for an unstamped one — the one
    answer that is never wrong. Rendering it as 'just now' would put a lie in
    the exact field the coordinator reads to decide whether to trust the rest."""
    if not ts:
        return "age unknown"
    d = max(0, int(now - ts))
    if d < 60:
        return f"{d}s ago"
    if d < 3600:
        return f"{d // 60}m ago"
    return f"{d // 3600}h{(d % 3600) // 60:02d}m ago"


def _item_note(e: StopEvent) -> str:
    if e.item:
        return f"held {e.item} ({e.item_status or 'status ?'})"
    if e.item_status == "?":
        return "item: could not read the tracker"
    return "no open item"


def _compose_reason(events: list[StopEvent], verdicts: dict, now: float,
                    deferred: int = 0) -> str:
    """One line per AGENT, not per event — and every line carries the three facts
    the old payload lacked: when, what it held, and whether it is free now.

    Collapsing by agent is not cosmetic. kelly emitted TWO events for one
    continuous stretch of work (turn boundaries), and two lines saying "kelly
    stopped" invite two decisions about one agent. The latest event wins; the
    count is still printed, because "this agent turned over 3 times" is itself a
    signal and hiding it would trade one wrong impression for another.
    """
    latest: dict[str, StopEvent] = {}
    counts: dict[str, int] = {}
    for e in sorted(events, key=lambda x: (x.ts, x.id)):
        counts[e.frm] = counts.get(e.frm, 0) + 1
        latest[e.frm] = e                          # sorted -> last one wins
    lines = [f"{len(latest)} agent(s) stopped — handle each (absorb / delegate / "
             f"escalate); they will NOT be redelivered. A stop is a TURN boundary, "
             f"so `now:` is the pane verdict taken just now, and it is the one to "
             f"act on:"]
    for name in sorted(latest):
        e = latest[name]
        tag = f" (ROSE: {e.reason})" if e.rose else (f" [{e.reason}]" if e.reason else "")
        # The shell count is the difference between "its turn ended" and "it is
        # finished" (internal-ref). Said in the destination's own words, because
        # the destination is the one about to book the item as done. Taken from
        # the LATEST event only: an earlier count is a fact about an earlier turn,
        # and re-asserting it here would report a shell that has since exited.
        if e.shells:
            tag += (f" — STILL RUNNING {e.shells} background shell(s): its TURN "
                    f"ended, its WORK may not have")
        # PAST THE CYCLE THRESHOLD (internal-ref), from the latest event's own
        # reading. The difference between "gennaro stopped" and "gennaro stopped as
        # a wall": a destination that hands the next item to a past-threshold agent
        # is piling onto one that must cycle first. context_k is None when the stop
        # was mid-turn (no footer) — not reported, so not asserted. Raw depth, no
        # "% of limit" — 400k is a cycle point, not the ceiling.
        if e.context_k is not None and e.context_k >= CYCLE_THRESHOLD_K:
            tag += (f" — PAST THE 400k CYCLE THRESHOLD at {int(e.context_k)}k: do "
                    f"NOT hand it the next item until it CHECKPOINTS state to its "
                    f"bead, THEN /clears")
        more = f" ({counts[name]} events)" if counts[name] > 1 else ""
        # BLOCKED ON A QUESTION (internal-ref). The bare verdict `waiting` is already
        # better than the `?` it replaces, but a coordinator reading this line is
        # deciding what to DO, and "waiting" alone does not say that the thing it is
        # waiting for is THEM. Spelled out here rather than left to be inferred,
        # because the whole failure was 7 stalled workers looking like 7 busy ones
        # and two of them sitting an hour on questions that were already answered.
        if verdicts.get(name) == triage.WAITING:
            tag += (" — BLOCKED ON A QUESTION in its pane: it is stopped until "
                    "someone answers. Answer it, or tell it to put the decision on "
                    "the bead and carry on")
        lines.append(f"  - {name} stopped {_age(e.ts, now)} — now: "
                     f"{verdicts.get(name, '?')} · {_item_note(e)}{tag}{more}")
    if deferred:
        lines.append(f"  ({deferred} more held back: those agents are mid-flight "
                     f"right now. They will be delivered when they actually stop.)")
    return "\n".join(lines)


def _drain(events: FilesEvents, me: str, reg=None, panes=None,
           shows_ready_ui=None, awaiting_answer=None, *, plate=None, rank=None) -> int:
    """Deliver MY events — minus the ones whose sender is still working. For an
    administrator, also append a prioritized workflow over fleet state.

    reg/panes/shows_ready_ui are optional so a caller with no pane backend still
    gets delivery (verdicts read `?`). Without them nothing is deferred: refusing
    to deliver on the strength of a check we did not run would be worse than the
    bug being fixed. plate/rank feed the admin's prioritized workflow.
    """
    now = time.time()
    verdicts: dict[str, str] = {}
    deferred = 0
    accept = None
    if reg is not None and panes is not None and shows_ready_ui is not None:
        def accept(ev: StopEvent) -> bool:            # noqa: F811 — the wired form
            nonlocal deferred
            if ev.frm not in verdicts:
                verdicts[ev.frm] = _liveness(reg, panes, shows_ready_ui, ev.frm,
                                             awaiting_answer)
            if verdicts[ev.frm] == triage.BUSY:
                deferred += 1
                return False                          # DEFER — still pending
            return True

    got = events.drain(me, accept)                 # BLOCK-ONCE happens in drain()
    if not got:
        # Nothing to act on -> NO block -> idle. This is now also the turn-boundary
        # case: every pending sender is still mid-flight, so there is no decision
        # to make and waking the destination would be the internal-ref bug itself.
        if deferred:
            print(f"stop_event: {deferred} event(s) held back — sender(s) still "
                  f"mid-flight", file=sys.stderr)
        return 0
    reason = _compose_reason(got, verdicts, now, deferred)
    # ADMIN ENRICHMENT: only when a stop event actually fired (rides BLOCK-ONCE),
    # so a persistently-idle fleet can never re-block the admin every stop. A bare
    # _drain(events, me) is unaffected — the gate is inside _compose_workflow.
    if reg is not None and panes is not None:
        extra = _compose_workflow(reg, panes, plate, rank, got, me)
        if extra:
            reason = reason + "\n\n" + extra
    # Deliver to the MODEL via the block protocol. reason, never systemMessage.
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def _compose_workflow(reg, panes, plate, rank, events, me: str) -> str:
    """Admin-only: a prioritized workflow over fleet state, appended to the drained
    stop events. Returns '' for a non-admin, or when nothing is actionable. NEVER
    raises — a down ranker degrades to the rule-based order; the hook must idle or
    deliver, never wedge on a backend."""
    try:
        if reg.get(me).role != "administrator":
            return ""
    except LookupError:
        return ""                                 # unknown identity -> no enrichment
    try:
        agents = [a for a in reg.all() if a.name != me]   # never prioritize itself
    except OSError:
        agents = []                               # no crew dir -> events-only workflow
    candidates = workflow.classify(agents, panes, plate)
    candidates = workflow.fold_events(candidates, events)
    try:
        candidates = (rank or NullRanker()).weigh(candidates)
    except RankUnavailable:
        pass                                      # degrade to the rule-based order
    return workflow.prioritize(candidates).render()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv[0] if argv else ""
    if mode not in ("send", "drain", "haul"):
        print("usage: python -m shantytown.stop_event send|drain|haul [--root DIR]",
              file=sys.stderr)
        return 2
    me = os.environ.get("SHANTY_AGENT")
    if not me:
        print("stop_event: $SHANTY_AGENT is unset — cannot resolve identity",
              file=sys.stderr)
        return 1
    root = _root(argv)
    reg = FilesRegistry(root / "crew")
    events = FilesEvents(root / "events")
    panes = Tmux()
    if mode == "send":
        return _send(reg, events, panes, me, root)
    if mode == "haul":
        return _haul(reg, panes, me, root)
    # shows_ready_ui is the RUNTIME's marker check (triage stays runtime-blind).
    # It reads only the screen, so the settings resolver it never calls is None.
    runtime = ClaudeRuntime(panes, lambda card: None, root=root)
    # Wire the fleet-state readers + ranker so an administrator's drain is enriched
    # with a prioritized workflow (a lead's/worker's is unaffected — the gate is
    # inside _compose_workflow). Ranker is opt-in: NullRanker (no backend, the
    # default) unless SHANTY_RANKER=policy asks for Hank/Quipu weighting.
    plate = lambda who: files_plate(FilesTracker(root / "items"), who)  # noqa: E731
    rank = PolicyRanker() if os.environ.get("SHANTY_RANKER") == "policy" else NullRanker()
    return _drain(events, me, reg, panes, runtime.shows_ready_ui,
                  runtime.awaiting_answer, plate=plate, rank=rank)


if __name__ == "__main__":
    raise SystemExit(main())
