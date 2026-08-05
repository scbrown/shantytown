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

from .inbox import is_decision, is_message


def _root(argv: list[str]) -> Path:
    """--root <dir>, else the shared discovery chain (deployment.resolve_root).

    ONE resolver, four callers. This function was written out longhand in the CLI
    and in each of the three hook entry points, which is the drift deployment.py
    exists to prevent — and it meant extending discovery would have had to be done
    four times or be done inconsistently.
    """
    if "--root" in argv:
        return Path(argv[argv.index("--root") + 1])
    return resolve_root()[0]


_DEFAULT_DARK = "arnold dearing ellie goldblum ian malcolm maldoon sentinel"


def dark_agents() -> set[str]:
    """The gastown-dark crew: agents that route NO stop event to this coordinator
    and strand any bead dispatched to them. Override via SHANTY_DARK_AGENTS (space-
    or comma-separated); the default mirrors the crew operating file.

    WHY A NAME DENYLIST AND NOT THE `send`-WIRING GATE (measured 2026-07-23, sattler).
    The wiring gate below excludes an agent that carries no `send` direction — but a
    respawned gastown agent DOES carry it: aegis-b686's masked-daemon cron / gt
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

    THE STRUCTURAL FIX FOR THE DARK-CREW TRAP (aegis-2j2r, measured
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
    not launch are not st's to feed (st_launched_agents).

    RETIRED CARDS ARE NOT FEEDABLE (aegis-w4k8n). Retiring a card stops `st tend`
    RESPAWNING that agent; it does not kill a pane that is already up. So a
    retired agent mid-turn stays live, stays idle, and — until this gate — read as
    dispatchable capacity. Measured after the roster cut: all SIX names this
    returned were retired, i.e. the true feedable count was zero and every name
    Rule Zero handed the coordinator was one `st go` would refuse.

    The gate lives HERE, in the one computation the soft alert (IdleFleetAlerter)
    and the hard stop gate share, precisely so they cannot disagree about who is
    free — a second opinion is the thing this function exists to prevent.
    """
    from . import triage as triage_mod
    from .runtime import asks_a_question, auth_expired, live_wiring
    # tend owns the retirement predicate; imported HERE rather than at module
    # level to match this function's other deferred imports and to keep
    # feed_check free of a top-level dependency on the supervisor.
    from .tend import is_retired

    dark = dark_agents()
    stamped = st_launched_agents(root) if root is not None else None
    out = []
    for ag in reg.all():
        if ag.role != "worker" or not ag.pane or not panes.exists(ag.pane):
            continue
        # Cheap, and FIRST among the card checks — the same ordering rule
        # tend._one states: a retirement test that runs after the logic it is
        # meant to veto is a test that can be reached too late. `retired` is
        # tri-state (None = not expressed), and every consumer tests truthiness.
        if is_retired(ag):
            continue                     # deliberately stopped -> not a target
        if ag.name in dark:
            continue                     # gastown-dark: respawns + carries send
                                         # wiring, but routes no stop to us (dark_agents)
        if stamped is not None and ag.name not in stamped:
            continue                     # no launch stamp -> not launched by st
                                         # -> not ours to feed (st_launched_agents)
        screen = panes.capture(ag.pane, attrs=True)
        plain = triage_mod.strip_attrs(screen)
        # auth_dead (aegis-arma): a login-expired pane renders idle, and counting
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

    WHY THIS EXISTS (aegis-arma follow-up, measured 2026-07-22). bd resolves its
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
# --limit 0 = UNLIMITED (GitHub #28). bd truncates machine-readable output
# SILENTLY: `bd ready --json` returned 10 of 174 and `bd list --json` 50 of
# 190, with empty stderr and exit 0. Every consumer here reasons about the
# WHOLE queue — Rule Zero asks "is there dispatchable work", the plate asks
# "what do I hold" — so a silently short list is a wrong answer, not a small
# one. The upstream bug is bd's; this is the consumer refusing to inherit it.
    r = subprocess.run(["bd", "ready", "--json", "--limit", "0"], capture_output=True, text=True,
                       timeout=20, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"bd ready failed: {r.stderr.strip()}")
    return json.loads(r.stdout)


def dispatchable(free: set, ready_beads) -> list[tuple[str, str]]:
    """Of the ready beads, those the COORDINATOR has to hand out: the UNASSIGNED
    ones, only.

    THE THREAD REINTERPRETATION (aegis-wjgt groundwork, Stiwi's call): an
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
        # A decision-gated bead is not implementer work — the coordinator must
        # not hand an unassigned one to a worker to "execute" either (aegis-2og7d).
        if not b.get("assignee") and not is_decision(b.get("labels")):
            out.append((b.get("id", "?"), b.get("title", "")))
    return out


def hauls(ready_beads, in_progress_beads=()) -> dict[str, list[str]]:
    """worker name -> the READY beads already assigned to them: each worker's
    own queue (the HAUL, in tracker-native terms — aegis-wjgt).

    A worker with a non-empty queue is SELF-FEEDING: excluded from the feedable
    free list (its next work is already determined; dispatching into it or
    alerting the coordinator about it are both noise). bd's assignee is a crew
    path (beads_aegis/crew/<name>) or a bare name; the trailing segment is the
    worker name, same parse the old dispatchable used.

    MESSAGES ARE NOT A QUEUE, and here that mistake is worse than on the haul.
    This function decides who is SELF-FEEDING, and a self-feeding worker is
    EXCLUDED from the Rule Zero feedable list. So an idle worker holding three
    unread `inbox:` items read as "its next work is already determined" — it
    vanished from the coordinator's free list and got dispatched nothing, while
    the gate that exists to stop the fleet going idle counted it as busy. Same
    predicate as the plate readers and the haul advance (inbox.is_message).

    IN_PROGRESS ITEMS COME FIRST, and they were previously absent ENTIRELY
    (aegis-ap4gm). An item the worker ALREADY STARTED is the strongest possible
    next-item signal — stronger than anything merely ready — so it ranks ahead.

    Only ASSIGNED ones are taken. An in_progress bead with an EMPTY assignee is a
    different defect and must not be papered over here: re-pooling (`bd update
    -a ""`) clears the assignee and leaves the status at in_progress, so the bead
    is in no haul, on no plate and out of `bd ready` — it leaves the board
    entirely, and the agent that handed it back did everything right. Pretending
    someone owns it would hide that; the fix is resetting the status to `open`.
    """
    out: dict[str, list[str]] = {}
    for b in list(in_progress_beads) + list(ready_beads):
        assignee = b.get("assignee")
        # A message is not a queue; NEITHER is a decision-gated bead. A worker
        # whose only ready item is a decision-needed bead has NO real queue, so
        # it must stay on the coordinator's feedable free list rather than read
        # as self-feeding (aegis-2og7d) — the same reasoning as messages above.
        if (not assignee or is_message(b.get("title", ""))
                or is_decision(b.get("labels"))):
            continue
        name = assignee.split("/")[-1]
        if not name:
            continue
        bid = b.get("id", "?")
        # De-dup defensively. The two sources are disjoint by bd's own status
        # semantics today; this must not yield a doubled queue entry if that
        # ever stops being true.
        if bid not in out.setdefault(name, []):
            out[name].append(bid)
    return out


class _Item:
    """The two attributes `governor.Verdict.admits` reads, off a bd JSON bead.

    A SHIM AND NOT A SECOND RULE. The alternative — re-implementing "does this
    priority clear the floor" here against the same dicts — is exactly the
    duplicated constant this bead forbids: two copies of a comparison whose sign
    is already documented as a foot-gun, in two files, drifting the first time a
    tier gains a field. So the ONE resolver `st go` uses is called with an object
    shaped the way it expects, and this class is the whole adaptation.
    """
    __slots__ = ("id", "priority")

    def __init__(self, bead: dict):
        self.id = bead.get("id", "?")
        p = bead.get("priority")
        # bd emits ints; anything unparseable becomes None, which `admits`
        # already refuses under a floor (it cannot be SHOWN to clear it).
        try:
            self.priority = None if p is None else int(p)
        except (TypeError, ValueError):
            self.priority = None


def throttle(ready: list[tuple[str, str]], beads, admits) -> tuple[list, list]:
    """Split the dispatchable list into (admitted, held) by the GOVERNOR's rule.

    WHY THIS EXISTS (aegis-diasw). Rule Zero and the governor contradicted each
    other in production: `st tend` reported a 50% tier admitting only P1-and-above
    while feed_check ordered a dispatch and named three P2 beads as its top
    candidates — every one of which `st go` then refused. A blocking stop hook
    demanding an action a second mechanism forbids has exactly one easy way out,
    and it is the wrong one: bump the P2 to P1 and the alarm stops.

    So "dispatchable" now means PASSES THE FLOOR, not merely open-and-unassigned.
    `admits` is the callable off the governor's own Verdict — the same object
    `st go` gates on — never a floor re-derived here.

    `admits` of None means no governor is configured, which is the default and
    must behave exactly as before: everything admitted, nothing held.
    """
    if admits is None:
        return list(ready), []
    by_id = {b.get("id"): b for b in beads}
    ok, held = [], []
    for bid, title in ready:
        why = admits(_Item(by_id.get(bid) or {"id": bid}))
        (held if why else ok).append((bid, title, why) if why else (bid, title))
    return ok, held


def held_reason(free: list[str], held: list[tuple[str, str, str]]) -> str:
    """Why the stop is being ALLOWED with idle workers and a full queue.

    THE POINT IS THAT IT SPEAKS. Rule Zero going quiet is the correct behaviour
    under an engaged tier and it is also exactly what a broken feeder looks like;
    the coordinator cannot tell them apart from silence, and the one it will
    assume is the one that makes it act.

    IT DOES NOT FORBID THE PRIORITY BUMP (ruled by Stiwi on aegis-diasw, which
    withdrew the decision that would have removed the escape hatch from `st go`).
    The hatch is legitimate BECAUSE the bump is recorded — bd history carries
    priority per revision with timestamps, so an inflation is visible and
    diffable after the fact. What was wrong was never that the hatch existed; it
    was that a BLOCKING stop hook pushed the coordinator toward it while the
    governor forbade the dispatch. Removing the push is the fix, and this
    function is the removal. So it states that idle is correct and stops there:
    a deliberate re-grade by an actor that has read this line is a judgement
    someone can audit, which is a different thing entirely from one manufactured
    by two mechanisms disagreeing.
    """
    why = held[0][2] if held else ""
    return (f"RULE ZERO YIELDS to the usage governor: {len(free)} idle "
            f"worker(s) ({', '.join(sorted(free))}) and {len(held)} ready "
            f"bead(s), 0 of which clear the priority floor. IDLE IS THE CORRECT "
            f"STATE — the throttle is holding, the feeder is not broken, and the "
            f"work is untouched. Nothing here needs doing. Governor says: {why}")


def _reason(free: list[str], ready: list[tuple[str, str]]) -> str:
    top = "; ".join(f"{bid} {title}"[:70] for bid, title in ready[:3])
    return (
        f"RULE ZERO — do not stop with the fleet idle. {len(free)} feedable "
        f"worker(s) IDLE ({', '.join(free)}) and {len(ready)} dispatchable bead(s) "
        f"ready. Dispatch before you stop (`st go <bead> <worker>`), then this stop "
        f"is allowed. Top ready: {top}.")


def governor_admits(root):
    """The governor's `item -> "" | why` for this root, or None if none is
    configured. A PURE READ (`persist=False`): asking whether work is dispatchable
    must never ratchet fleet policy — `st tend` is the one writer of the engaged
    tier, and a stop hook that advanced hysteresis would make the governor's state
    depend on how often agents happened to stop.

    Returns None on ANY failure, which is the fail-open direction this whole
    module is built on: an unreadable governor must degrade to the old behaviour
    (nag about everything), never to a silent refusal to nag about anything.
    """
    try:
        from . import config
        from . import governor as gov_mod
        cfg, _err = config.load_or_default(Path(root))
        if not cfg.governor.active:
            return None
        gov = gov_mod.Governor(cfg.governor, gov_mod.reader_for(cfg.governor),
                               gov_mod.FilesGovernorState(Path(root)))
        return gov.evaluate(persist=False).admits
    except Exception:      # noqa: BLE001 — no governor readable -> ungoverned
        return None


def gate_inputs(root, reg, panes, runtime, me: str | None = None, admits=None):
    """(free feedable workers, dispatchable ready beads, HELD-BY-GOVERNOR beads).

    The third value is the aegis-diasw fix and it is not decoration: with a tier
    engaged, `ready` legitimately goes empty and the stop is correctly allowed —
    but "the throttle is holding" and "the feeder is broken" then look IDENTICAL
    to the coordinator, which is the failure mode this repo keeps paying for.
    Held is what lets the caller say WHICH.

    Lifted verbatim out of main() so the unified stop decision (stop_policy, rank
    2) and this hook compute them ONE way. The logic below is the part of
    feed_check that was always right; what it should not keep owning is the
    verdict, because a second independently-blocking Stop hook is what made a
    documented hibernate policy inert (docs/stop-policy-spec.md).

    Raises rather than fail-opening: the CALLER owns that policy, and both callers
    do it. Swallowing here would hide a broken gate from both of them.
    """
    free = free_feedable_workers(reg, panes, runtime, root=root)
    if not free:
        return [], [], []
    # bd_cwd, not the ambient cwd, even though the hook usually fires in the
    # admin's workspace: "usually" is how the tend caller silently never
    # fired (see bd_cwd). None still falls back to ambient — fail-open.
    ready_beads = _bd_ready(bd_cwd(reg))
    # HAULING WORKERS ARE NOT THE COORDINATOR'S TO FEED (aegis-wjgt
    # groundwork): an idle worker whose queue is already assigned self-feeds
    # — holding the coordinator's stop hostage over one is the exact inverse
    # of Rule Zero's purpose. The gate blocks only for (idle unhauled
    # workers) x (unassigned ready work).
    # IN_PROGRESS COUNTS AS A HAUL (aegis-ap4gm). `bd ready` cannot see an item
    # someone already started, so a worker holding one read as unhauled AND idle
    # and the coordinator was asked to feed work it already owned. Fails open to
    # the old, narrower answer if the extra bd call fails.
    # IN_PROGRESS COUNTS AS A HAUL (aegis-ap4gm). `bd ready` cannot see an item
    # someone already started, so a worker holding one read as unhauled AND idle
    # and the coordinator was asked to feed work it already owned. Fail open to
    # the old, narrower answer: this widens a queue, so a bd hiccup must degrade
    # to today's behaviour, never wedge the gate the whole crew loop hangs on.
    try:
        active = bd_in_progress(bd_cwd(reg))
    except Exception:      # noqa: BLE001 — see fail-open above
        active = []
    free = [w for w in free if w not in hauls(ready_beads, active)]
    if not free:
        return [], [], []
    # THE GOVERNOR IS ASKED LAST, over the beads that survived every other
    # filter — so `held` names exactly the work that WOULD have been dispatched
    # and is not, which is the number the coordinator needs to tell a holding
    # throttle from a broken feeder.
    ok, held = throttle(dispatchable(set(free), ready_beads), ready_beads,
                        admits if admits is not None else governor_admits(root))
    return free, ok, held


def main(argv: list[str] | None = None) -> int:
    """The standalone Rule Zero hook.

    SUPERSEDED as a hook by stop_policy, which folds this into one verdict — but
    kept invokable so a settings file still naming it keeps working. A
    half-deployed fleet is then degraded-but-correct rather than broken.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        from .files import FilesRegistry
        from .runtime import ClaudeRuntime
        from .tmux import Tmux, declared_socket

        root = _root(argv)
        reg = FilesRegistry(root / "crew")
        panes = Tmux(socket=declared_socket(root))
        runtime = ClaudeRuntime(panes, lambda _c: None, root=root)

        free, ready, held = gate_inputs(root, reg, panes, runtime)
        if not free or not ready:
            # ALLOWED — but not SILENTLY, when the governor is why (aegis-diasw).
            # An idle fleet under an engaged tier is the CORRECT state, and it
            # is indistinguishable from a broken feeder unless something says so.
            # stderr, because this path allows the stop and a JSON payload on
            # stdout is the block protocol.
            if free and held:
                print(held_reason(free, held), file=sys.stderr)
            return 0                     # nothing to feed -> allow the stop

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

def haul_feed_message(nid: str, title: str, rest: int, headroom: str = "",
                      repeats: int = 0) -> str:
    """The advance instruction: the specific next bead, claimed and named.

    The last line is the RELEASE affordance (aegis-tgvtg). The haul re-serves any
    open, assigned, ready bead of yours — that is how it advances — so a bead you
    have finished with, or that is not yours to work, keeps coming back until its
    assignee is cleared. Setting status alone does NOT release it: an assigned
    bead reads as your ready work regardless of status, which is the whole reason
    a deliberate 'I'm done here' was silently re-claimed. Naming the exit at the
    exact moment of the re-serve is the fix; clearing the assignee is the exit.

    `headroom` AND `repeats` ARE THE TWO SENTENCES THIS MESSAGE WAS MISSING
    (aegis-xxae9, items 3 and 4).

    "The coordinator was not pinged: this queue is yours" is TRUE and it was read
    as standing authority to keep going, four items deep into an unattended run.
    It cannot simply be deleted — a self-feeding queue does have to say that
    nobody is coming — so it now carries the remaining budget beside it.
    Authority with a number attached is a different sentence.

    And a REPEAT is now visibly a repeat. Being handed the same bead back reads
    as an instruction to continue; it is actually just the re-serve rule, which
    means nothing at all. Saying so where it happens is the whole fix.
    """
    t = (title or "")[:80]
    again = (f"⚠ THIS IS THE SAME BEAD YOU WERE ALREADY SERVED "
             f"{'twice' if repeats > 1 else 'once'} this stretch. That is the "
             f"re-serve rule, NOT a decision that you should keep at it — an "
             f"assigned, open, ready bead comes back until you release it. If "
             f"you already judged it done, blocked, or not yours, act on that "
             f"judgement below rather than re-reading it. " if repeats else "")
    # No budget declared -> the sentence stays exactly as it was. A deployment
    # that has not armed the ceiling must not be told about headroom it has none
    # of; a caveat with no number behind it is just noise to learn to skip.
    authority = (f"The coordinator was not pinged: this queue is yours to work, "
                 f"within the session budget — {headroom}. " if headroom else
                 f"The coordinator was not pinged: this queue is yours. ")
    return (
        f"HAUL: next on your haul: {nid} ({t}). {again}Read it (`bd show {nid}`) "
        f"and execute; close it when done and the haul advances itself ({rest} "
        f"more after this). If your context is deep, checkpoint + /clear FIRST — "
        f"the haul survives it. {authority}"
        f"Not this one? DONE -> `bd close {nid}`. BLOCKED/gated (nobody should "
        f"work it yet) -> `bd defer {nid}`, which takes it OUT of the ready pool "
        f"until you undo. Valid work but not yours -> `bd update {nid} -a \"\"` "
        f"hands it back for another agent. Note: a bare status change won't stop "
        f"the re-serve, and clearing the assignee only RE-POOLS it — a still-ready "
        f"bead is grabbed by the next idle agent — so defer or close to truly park.")


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
    r = subprocess.run(["bd", "list", "--status", "in_progress", "--json",
                        "--limit", "0"],
                       capture_output=True, text=True, timeout=20, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"bd list failed: {r.stderr.strip()}")
    return json.loads(r.stdout)


def bd_blocked(cwd: str | None) -> list[dict]:
    """`bd list --status blocked --json` — the population NOTHING else can see.

    A separate query on purpose: `bd ready` excludes blocked BY DEFINITION, so
    `_bd_ready()` cannot reach these. Measured on the live store: 126 ready, 0 of
    them blocked; 16 blocked overall, all 16 assigned to agents. Re-surfacing
    them therefore needs its own read, not a filter over an existing one.

    Raises; callers fail open.
    """
    r = subprocess.run(["bd", "list", "--status", "blocked", "--json",
                        "--limit", "0"],
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
