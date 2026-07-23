"""feed_check — the administrator's Rule Zero HARD GATE (internal-ref).

`python -m shantytown.feed_check --root <root>`, a Stop hook that runs beside the
administrator's drain (settings_for_role administrator). It BLOCKS the
coordinator's own stop while FREE feedable workers AND DISPATCHABLE beads both
exist, so the coordinator physically cannot go idle with the fleet idle.

WHY A HOOK AND NOT A RULE. sattler stalled — handled one question, stopped, left
nine agents idle with a full ready queue. A rule in the operating file relies on
the coordinator remembering; a Stop hook does not. Claude Code Stop hooks may
return {"decision":"block","reason":...}, which prevents the stop and injects the
reason as the coordinator's next input — so the coordinator is forced back to work
instead of idling. This is the mechanism-over-memory version (internal-ref) aimed at
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
  the stop-event `send` wiring (internal-ref). A gastown-dark worker carries none —
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


_DEFAULT_DARK = "arnold dearing ellie goldblum ian malcolm maldoon sentinel"


def dark_agents() -> set[str]:
    """The gastown-dark crew: agents that route NO stop event to this coordinator
    and strand any bead dispatched to them. Override via SHANTY_DARK_AGENTS (space-
    or comma-separated); the default mirrors the crew operating file.

    WHY A NAME DENYLIST AND NOT THE `send`-WIRING GATE (measured 2026-07-23, sattler).
    The wiring gate below excludes an agent that carries no `send` direction — but a
    respawned gastown agent DOES carry it: internal-ref's masked-daemon cron / gt
    handoff-respawn brings these panes back within seconds of a kill, re-primed with
    the shantytown worker settings (hence the send hook). So the wiring gate cannot
    tell them apart, and killing them is whack-a-mole (st stop refuses them as
    not-st-owned; a raw tmux kill is undone by the respawner one interval later).
    They rendered `idle`, tripped Rule Zero on every coordinator stop, and a dispatch
    to one stranded the bead in_progress on a pane with no live consumer (8 beads
    stranded before this fix). A name denylist is the only respawn-proof exclusion."""
    raw = os.environ.get("SHANTY_DARK_AGENTS", _DEFAULT_DARK)
    return {n for n in raw.replace(",", " ").split() if n}


def st_launched_agents(root) -> set[str] | None:
    """Agents with a launch stamp under <root>/launched — the ones `st new`
    itself started. None = the store is missing, unreadable, or EMPTY: we
    CANNOT TELL who is ours, so the caller must apply NO ownership gate (an
    empty store proves nothing about ownership; a fresh deployment with no
    stamps yet must not starve its whole fleet).

    THE STRUCTURAL FIX FOR THE DARK-CREW TRAP (internal-ref, measured
    2026-07-23). The gastown crew-watchdog respawns its own fleet every 3
    minutes, re-primed with this deployment's worker settings — so those panes
    carry the `send` wiring the feedability gate keys on, while routing no
    stop event to this coordinator and stranding every bead dispatched to
    them. The name denylist (dark_agents above) shields the known eight; this
    gate is the general form: st only feeds agents st launched, and the launch
    stamp (launched.py, written by `st new` at launch) is precisely that
    signal — the same ownership fact behind `st stop`'s refusal to kill panes
    it does not own. Measured at introduction: all 10 live st workers
    stamped, all 8 gastown-respawned panes unstamped — perfect separation."""
    try:
        d = Path(root) / "launched"
        return {p.stem for p in d.glob("*.json")} or None
    except OSError:
        return None


def free_feedable_workers(reg, panes, runtime, root=None) -> list[str]:
    """IDLE workers st can actually dispatch to — the same idle verdict `st crew`
    shows, gated on the `send` wiring so a dark worker is never counted as free.
    When `root` is given, additionally gated on the launch stamp: agents st did
    not launch are not st's to feed (st_launched_agents)."""
    from . import triage as triage_mod
    from .runtime import asks_a_question, auth_expired, live_wiring

    dark = dark_agents()
    stamped = st_launched_agents(root) if root is not None else None
    out = []
    for ag in reg.all():
        if ag.role != "worker" or not ag.pane or not panes.exists(ag.pane):
            continue
        if ag.name in dark:
            continue                     # gastown-dark: respawns + carries send
                                         # wiring, but routes no stop to us (dark_agents)
        if stamped is not None and ag.name not in stamped:
            continue                     # no launch stamp -> not launched by st
                                         # -> not ours to feed (st_launched_agents)
        screen = panes.capture(ag.pane, attrs=True)
        plain = triage_mod.strip_attrs(screen)
        # auth_dead (internal-ref): a login-expired pane renders idle, and counting
        # it feedable is the measured failure — the coordinator was BLOCKED from
        # stopping to go feed nine agents none of which could run a single call.
        # An auth-dead worker's verdict is AUTH_DEAD, not IDLE, so it falls out
        # of `free` here — dead panes must never hold the coordinator hostage.
        state = triage_mod.work_state(
            screen, runtime.shows_ready_ui(plain),
            awaiting=asks_a_question(runtime, plain),
            auth_dead=auth_expired(runtime, plain))
        if state != triage_mod.IDLE:
            continue
        wiring = live_wiring(ag.pane, panes.cmdline)
        if wiring is None or "send" not in wiring.directions:
            continue                     # dark or unreadable -> not feedable
        out.append(ag.name)
    return sorted(out)


def bd_cwd(reg) -> str | None:
    """The directory `bd` must resolve its store FROM: the ADMINISTRATOR's
    workspace, off its card. None = could not resolve (no admin, no workspace).

    WHY THIS EXISTS (internal-ref follow-up, measured 2026-07-22). bd resolves its
    store from the ambient cwd, and 'the environment the crew runs in' is only
    the right environment for the STOP HOOK — it fires inside the admin's own
    workspace. The tend loop is a different caller: it runs wherever the
    operator happened to start it, and the live one ran from a checkout with no
    beads store at all. So `bd ready` raised 'no beads database found' on EVERY
    sweep, the alerter's fail-open swallowed it, and the nk0e idle-fleet push
    never fired once — for two days, silently, while the hard gate (same
    computation, right cwd) worked. The admin's workspace is where the
    coordinator itself runs bd, so it is the one directory that is correct for
    every caller.
    """
    for card in reg.all():
        if card.role == "administrator":
            if not card.workspace:
                return None
            # WALK UP to the nearest .beads. The workspace itself does not
            # resolve (measured): each crew workspace is its own git clone and
            # bd stops resolving at the clone boundary, so `bd ready` fails
            # even from the admin's own directory — the store lives at the RIG
            # ROOT above it. We deliberately walk past the git boundary bd
            # respects, because the card's workspace is deployment truth about
            # WHERE THIS FLEET'S RIG IS in a way the ambient cwd never was.
            p = Path(card.workspace)
            for anc in (p, *p.parents):
                if (anc / ".beads").is_dir():
                    return str(anc)
            return None
    return None


def _bd_ready(cwd: str | None = None) -> list[dict]:
    """`bd ready --json` -> the ready (unblocked, open) beads, or raise.

    `cwd` is where bd resolves its store from (see bd_cwd). None falls back to
    the ambient cwd — correct for the stop hook, a coin-flip for anything else;
    a failure propagates to the caller's fail-open."""
    r = subprocess.run(["bd", "ready", "--json"], capture_output=True, text=True,
                       timeout=20, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"bd ready failed: {r.stderr.strip()}")
    return json.loads(r.stdout)


def dispatchable(free: set, ready_beads) -> list[tuple[str, str]]:
    """Of the ready beads, those the COORDINATOR has to hand out: the UNASSIGNED
    ones, only.

    THE THREAD REINTERPRETATION (internal-ref groundwork, Stiwi's call): an
    assigned bead is its worker's OWN QUEUE, not coordinator-dispatch material.
    This used to also count beads assigned to a free worker — which made the
    coordinator the delivery mechanism for work the worker already owned (N
    pings + N manual go's, measured by sattler doing exactly that by hand all
    evening). Under haul semantics the worker's queue self-feeds; the
    coordinator's job is only the work NOBODY owns. `free` is still taken so
    the signature survives; unassigned beads are claimable by anyone free."""
    _ = free
    out = []
    for b in ready_beads:
        if not b.get("assignee"):
            out.append((b.get("id", "?"), b.get("title", "")))
    return out


def hauls(ready_beads) -> dict[str, list[str]]:
    """worker name -> the READY beads already assigned to them: each worker's
    own queue (the HAUL, in tracker-native terms — internal-ref).

    A worker with a non-empty queue is SELF-FEEDING: excluded from the feedable
    free list (its next work is already determined; dispatching into it or
    alerting the coordinator about it are both noise). bd's assignee is a crew
    path (beads_aegis/crew/<name>) or a bare name; the trailing segment is the
    worker name, same parse the old dispatchable used."""
    out: dict[str, list[str]] = {}
    for b in ready_beads:
        assignee = b.get("assignee")
        if not assignee:
            continue
        name = assignee.split("/")[-1]
        if name:
            out.setdefault(name, []).append(b.get("id", "?"))
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

        free = free_feedable_workers(reg, panes, runtime, root=root)
        if not free:
            return 0                     # nobody free -> allow the stop
        # bd_cwd, not the ambient cwd, even though the hook usually fires in the
        # admin's workspace: "usually" is how the tend caller silently never
        # fired (see bd_cwd). None still falls back to ambient — fail-open.
        ready_beads = _bd_ready(bd_cwd(reg))
        # HAULING WORKERS ARE NOT THE COORDINATOR'S TO FEED (internal-ref
        # groundwork): an idle worker whose queue is already assigned self-feeds
        # — holding the coordinator's stop hostage over one is the exact inverse
        # of Rule Zero's purpose. The gate blocks only for (idle unhauled
        # workers) x (unassigned ready work).
        free = [w for w in free if w not in hauls(ready_beads)]
        if not free:
            return 0                     # everyone idle is self-feeding -> allow
        ready = dispatchable(set(free), ready_beads)
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


# --- the haul advance's shared voice (stop-hook trigger + tend trigger) ------
#
# Two triggers, ONE advance: the worker's own Stop hook fires it at a stop
# (instant), and tend fires it for a worker that is ALREADY idle — an idle
# worker never stops again on its own, so a queue loaded after it idled would
# sit forever (measured: the one idle worker at fleet queue-load needed a
# manual bootstrap; every mid-turn worker advanced fine). Same message, same
# claim, same handoff line — built here so the two can never drift.

def haul_feed_message(nid: str, title: str, rest: int) -> str:
    """The advance instruction: the specific next bead, claimed and named."""
    t = (title or "")[:80]
    return (
        f"HAUL: next on your haul: {nid} ({t}). Read it (`bd show {nid}`) and "
        f"execute; close it when done and the haul advances itself ({rest} more "
        f"after this). If your context is deep, checkpoint + /clear FIRST — the "
        f"haul survives it. The coordinator was not pinged: this queue is yours.")


def haul_handoff_message(context_k: float, line_k: float) -> str:
    """Past the handoff line: shed context first; the haul resumes itself."""
    return (
        f"HAUL HANDOFF: you are at {int(context_k)}k — past the {int(line_k)}k "
        f"handoff line (60% of the window). Do NOT start the next item. (1) "
        f"CHECKPOINT anything unwritten to the bead trail now; (2) run /clear. "
        f"Your haul resumes automatically on the fresh context.")


def bd_in_progress(cwd: str | None) -> list[dict]:
    """`bd list --status in_progress --json` — the active-anchor set. Raises;
    callers fail open."""
    r = subprocess.run(["bd", "list", "--status", "in_progress", "--json"],
                       capture_output=True, text=True, timeout=20, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"bd list failed: {r.stderr.strip()}")
    return json.loads(r.stdout)


def bd_claim(cwd: str | None, bead_id: str) -> None:
    """Claim a bead in_progress — the dispatcher's write, shared by both
    advance triggers so the tracker shows the truth and the worker's next stop
    sees an active anchor. Raises; callers treat a failed claim as best-effort
    (the instruction tells the worker to read the bead either way)."""
    r = subprocess.run(["bd", "update", bead_id, "--status", "in_progress",
                        "--json"],
                       capture_output=True, text=True, timeout=20, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"bd update failed: {r.stderr.strip()}")
