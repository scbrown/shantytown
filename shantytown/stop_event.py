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
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

from . import workflow
from .events import FilesEvents
from .files import FilesRegistry, FilesTracker, plate as files_plate
from .policy import NullRanker, PolicyRanker
from .protocols import RankUnavailable
from .tier import route_stop
from .tmux import Tmux


def _root(argv: list[str]) -> Path:
    # --root <dir>, else $SHANTY_ROOT, else cwd/.shanty (same default as the CLI).
    if "--root" in argv:
        return Path(argv[argv.index("--root") + 1])
    env = os.environ.get("SHANTY_ROOT")
    return Path(env) if env else Path.cwd() / ".shanty"


def _lead_is_up(reg: FilesRegistry, panes) -> "callable":
    """route_stop asks 'is this lead reachable?' — answer from the pane, so a
    down lead makes the event RISE to the admin (Q3) rather than sit for a reader
    that will never come."""
    def up(name: str) -> bool:
        try:
            lead = reg.get(name)
        except LookupError:
            return False
        return bool(lead.pane) and panes.exists(lead.pane)
    return up


def _send(reg: FilesRegistry, events: FilesEvents, panes, me: str) -> int:
    try:
        routing = route_stop(reg, me, lead_is_up=_lead_is_up(reg, panes))
    except LookupError as e:
        # nowhere for the stop to go (no lead AND no administrator). This is a
        # real misconfiguration, surfaced — not swallowed. Non-zero so it shows.
        print(f"stop_event send: {e}", file=sys.stderr)
        return 1
    reason = routing.reason.value if routing.reason else None
    ev = events.persist(to=routing.to, frm=me, reason=reason, rose=routing.rose)
    # Silent on stdout (a non-blocking Stop hook's stdout is discarded anyway);
    # a terse stderr line is useful when a human runs it by hand.
    print(f"stop_event: {me} stopped -> persisted {ev.id} to {routing.to}"
          + (f" (ROSE: {reason})" if routing.rose else ""), file=sys.stderr)
    return 0


def _compose_reason(events) -> str:
    lines = [f"{len(events)} stop event(s) routed to you — handle each (absorb / "
             f"delegate / escalate); they will NOT be redelivered:"]
    for e in events:
        tag = f" (ROSE: {e.reason})" if e.rose else (f" [{e.reason}]" if e.reason else "")
        lines.append(f"  - {e.frm} stopped{tag}")
    return "\n".join(lines)


def _drain(events: FilesEvents, me: str, *, reg=None, panes=None,
           plate=None, rank=None) -> int:
    got = events.drain(me)                        # BLOCK-ONCE happens in drain()
    if not got:
        return 0                                  # nothing pending -> idle, no block
    reason = _compose_reason(got)
    # ADMIN ENRICHMENT (opt-in via the reg/panes wiring main() passes; a bare
    # _drain(events, me) — the shape the tests and any non-admin caller use — is
    # byte-identical to before). It RIDES `got`: only when a stop event actually
    # fired does the admin get the prioritized workflow, so a persistently-idle
    # fleet can never re-block the admin every stop (the BLOCK-ONCE guarantee).
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
    if mode not in ("send", "drain"):
        print("usage: python -m shantytown.stop_event send|drain [--root DIR]",
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
    if mode == "send":
        return _send(reg, events, Tmux(), me)
    # DRAIN. Wire the fleet-state readers + ranker so an administrator's drain is
    # enriched with a prioritized workflow (a lead's/worker's is unaffected — the
    # gate is inside _compose_workflow). Ranker is opt-in: NullRanker (no backend,
    # the default) unless SHANTY_RANKER=policy asks for Hank/Quipu weighting.
    plate = lambda who: files_plate(FilesTracker(root / "items"), who)  # noqa: E731
    rank = PolicyRanker() if os.environ.get("SHANTY_RANKER") == "policy" else NullRanker()
    return _drain(events, me, reg=reg, panes=Tmux(), plate=plate, rank=rank)


if __name__ == "__main__":
    raise SystemExit(main())
