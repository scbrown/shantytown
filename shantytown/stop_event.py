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

from .events import FilesEvents
from .files import FilesRegistry
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


def _drain(events: FilesEvents, me: str) -> int:
    got = events.drain(me)                        # BLOCK-ONCE happens in drain()
    if not got:
        return 0                                  # nothing pending -> idle, no block
    # Deliver to the MODEL via the block protocol. reason, never systemMessage.
    print(json.dumps({"decision": "block", "reason": _compose_reason(got)}))
    return 0


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
    return _drain(events, me)


if __name__ == "__main__":
    raise SystemExit(main())
