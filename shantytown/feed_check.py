"""feed_check — the administrator's Rule Zero HARD GATE (aegis-hfta).

`python -m shantytown.feed_check --root <root>`, a Stop hook that runs beside the
administrator's drain (settings_for_role administrator). It BLOCKS the
coordinator's own stop while FREE feedable workers AND DISPATCHABLE beads both
exist, so the coordinator physically cannot go idle with the fleet idle.

WHY A HOOK AND NOT A RULE. sattler stalled — handled one question, stopped, left
nine agents idle with a full ready queue. A rule in the operating file relies on
the coordinator remembering; a Stop hook does not. Claude Code Stop hooks may
return {"decision":"block","reason":...}, which prevents the stop and injects the
reason as the coordinator's next input — so the coordinator is forced back to work
instead of idling. This is the mechanism-over-memory version (aegis-mt0r) aimed at
the coordinator, and the hard-gate sibling of tend's soft idle-fleet push.

SELF-TERMINATING, NOT A LOOP. The block is gated on the REAL state (free>0 AND
dispatchable>0), which the coordinator resolves by DISPATCHING. Each dispatch drops
`free`; when free hits 0 (or no dispatchable work remains), the next stop is
ALLOWED. It terminates on the RIGHT condition — the fleet being fed — never on a
loop counter. Feed everyone and it lets you stop.

FAIL OPEN, non-negotiable. If the registry, tmux, or bd is unreachable, or ANYTHING
errors, the stop is ALLOWED (exit 0, no block). A hook that wedges the admin's stop
on a transient bd hiccup is worse than the stall it prevents. Every path here is
wrapped so the block is emitted ONLY when we are certain both conditions hold; all
else — including every exception — allows the stop.

Two definitions carry the "never false-trap" constraint:

  FREE = FEEDABLE. A free worker is one that is IDLE and whose LIVE PROCESS carries
  the stop-event `send` wiring (aegis-0v97). A gastown-dark worker carries none —
  it cannot report, so dispatching to it is into a black hole, and its idleness is
  NOT a reason to block. Unreadable wiring counts as NOT feedable (the safe
  direction: exclude, so a transient read never traps the coordinator).

  DISPATCHABLE = ACTUALLY FEEDABLE, not merely open. A ready bead assigned to a
  dark worker is stuck, not dispatchable. So a bead counts only if it is unassigned
  (claimable by any free worker) or assigned to a free-feedable one. A board of
  all-dark-assigned beads is not dispatchable -> allow the stop.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path


def _root(argv: list[str]) -> Path:
    # Same precedence as the stop_event hooks: --root, else $SHANTY_ROOT, else
    # cwd/.shanty. The Stop hook bakes an absolute --root, so this resolves the
    # real store no matter which workspace the admin was launched in.
    if "--root" in argv:
        return Path(argv[argv.index("--root") + 1])
    env = os.environ.get("SHANTY_ROOT")
    return Path(env) if env else Path.cwd() / ".shanty"


def free_feedable_workers(reg, panes, runtime) -> list[str]:
    """IDLE workers st can actually dispatch to — the same idle verdict `st crew`
    shows, gated on the `send` wiring so a dark worker is never counted as free."""
    from . import triage as triage_mod
    from .runtime import asks_a_question, live_wiring

    out = []
    for ag in reg.all():
        if ag.role != "worker" or not ag.pane or not panes.exists(ag.pane):
            continue
        screen = panes.capture(ag.pane, attrs=True)
        plain = triage_mod.strip_attrs(screen)
        state = triage_mod.work_state(
            screen, runtime.shows_ready_ui(plain),
            awaiting=asks_a_question(runtime, plain))
        if state != triage_mod.IDLE:
            continue
        wiring = live_wiring(ag.pane, panes.cmdline)
        if wiring is None or "send" not in wiring.directions:
            continue                     # dark or unreadable -> not feedable
        out.append(ag.name)
    return sorted(out)


def _bd_ready() -> list[dict]:
    """`bd ready --json` -> the ready (unblocked, open) beads, or raise. bd resolves
    its own store from the environment the crew runs in; a failure here propagates
    to main's fail-open."""
    r = subprocess.run(["bd", "ready", "--json"], capture_output=True, text=True,
                       timeout=20)
    if r.returncode != 0:
        raise RuntimeError(f"bd ready failed: {r.stderr.strip()}")
    return json.loads(r.stdout)


def dispatchable(free: set, ready_beads) -> list[tuple[str, str]]:
    """Of the ready beads, those a FREE worker could take now: unassigned (claimable
    by anyone) or already assigned to a free-feedable worker. A bead assigned to a
    dark or busy agent is excluded — it is not something to hand a free worker."""
    out = []
    for b in ready_beads:
        assignee = b.get("assignee")
        # bd stores the assignee as a crew path (beads_aegis/crew/<name>) or a bare
        # name, and often not at all (claim-based flow); compare the trailing name.
        name = assignee.split("/")[-1] if assignee else ""
        if not name or name in free:
            out.append((b.get("id", "?"), b.get("title", "")))
    return out


def _reason(free: list[str], ready: list[tuple[str, str]]) -> str:
    top = "; ".join(f"{bid} {title}"[:70] for bid, title in ready[:3])
    return (
        f"RULE ZERO — do not stop with the fleet idle. {len(free)} feedable "
        f"worker(s) IDLE ({', '.join(free)}) and {len(ready)} dispatchable bead(s) "
        f"ready. Dispatch before you stop (`st go <bead> <worker>`), then this stop "
        f"is allowed. Top ready: {top}.")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        from .files import FilesRegistry
        from .runtime import ClaudeRuntime
        from .tmux import Tmux, declared_socket

        root = _root(argv)
        reg = FilesRegistry(root / "crew")
        panes = Tmux(socket=declared_socket(root))
        runtime = ClaudeRuntime(panes, lambda _c: None, root=root)

        free = free_feedable_workers(reg, panes, runtime)
        if not free:
            return 0                     # nobody free -> allow the stop
        ready = dispatchable(set(free), _bd_ready())
        if not ready:
            return 0                     # no dispatchable work -> allow the stop

        # Both conditions hold, and we are certain: BLOCK, with an actionable
        # reason. This is the only path that prints anything.
        print(json.dumps({"decision": "block", "reason": _reason(free, ready)}))
        return 0
    except Exception:
        # FAIL OPEN. Any error — registry, tmux, bd, parse — allows the stop.
        # Never trap the coordinator on a broken check.
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
