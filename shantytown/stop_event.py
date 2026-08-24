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

A STOP EVENT IS A TURN BOUNDARY, NOT AN IDLE AGENT (aegis-w9z1). Claude Code's
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
from .deployment import deployment_default, resolve_root
from .events import FilesEvents, StopEvent
from .inbox import is_decision, is_message
from .files import FilesRegistry, FilesTracker, plate as files_plate
from .policy import NullRanker, PolicyRanker
from .protocols import RankUnavailable
from .runtime import ClaudeRuntime, live_wiring
from .stopped import FilesStops
from .tier import LeadStatus, is_governance, route_stop
from .triage import running_shells, context_tokens_k, CYCLE_THRESHOLD_K
from .tmux import Tmux


# An active Codex anchor needs a continuation prompt, but that same prompt is
# emitted from every Stop hook.  Without a rate limit a standing drain anchor
# turns ordinary turn boundaries into coordinator churn (six stops in 70s,
# aegis-nvz3s).  The first prompt is always immediate; subsequent prompts for
# the *same* anchor back off exponentially.  A new anchor is a new piece of
# work and starts fresh.
HAUL_RESUME_INITIAL_BACKOFF_S = 60.0
HAUL_RESUME_MAX_BACKOFF_S = 15 * 60.0


def _haul_resume_marker(root: Path, agent: str) -> Path:
    return Path(root) / "haul_resume" / f"{agent}.json"


def _allow_haul_resume(root: Path | None, agent: str, nid: str,
                       now: float | None = None) -> bool:
    """Whether this active anchor may emit a continuation prompt now.

    The marker is advisory only: unreadable state, a failed write, or a test
    with no root all allow the prompt.  A limiter must never strand a Codex
    worker merely because its small local state file is unavailable.
    """
    if root is None:
        return True
    try:
        d = json.loads(_haul_resume_marker(root, agent).read_text(encoding="utf-8"))
        if d.get("anchor") != nid:
            return True
        last = float(d["at"])
        prompts = max(1, int(d.get("prompts", 1)))
        delay = min(HAUL_RESUME_INITIAL_BACKOFF_S * (2 ** (prompts - 1)),
                    HAUL_RESUME_MAX_BACKOFF_S)
        current = time.time() if now is None else now
        # A backwards or malformed clock is not evidence to suppress work.
        return current < last or current - last >= delay
    except FileNotFoundError:
        return True
    except Exception:                    # noqa: BLE001
        return True


def _mark_haul_resume(root: Path | None, agent: str, nid: str,
                      now: float | None = None) -> None:
    """Best-effort record of an emitted resume prompt; never blocks the hook."""
    if root is None:
        return
    try:
        p = _haul_resume_marker(root, agent)
        prompts = 1
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
            if old.get("anchor") == nid:
                prompts = max(1, int(old.get("prompts", 1))) + 1
        except Exception:                # noqa: BLE001
            pass
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"anchor": nid,
                                 "at": time.time() if now is None else now,
                                 "prompts": prompts}), encoding="utf-8")
    except Exception:                    # noqa: BLE001
        pass


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


def _lead_is_up(reg: FilesRegistry, panes) -> "callable":
    """route_stop asks 'is this lead reachable?' — and REACHABLE MEANS IT WILL
    DRAIN, not that something answers to its name (dearing, aegis-0v97).

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
    def up(name: str) -> "LeadStatus":
        # RETURNS A REASON, NOT JUST A BOOL. Four distinct states collapsed into
        # False here, and two of them want opposite actions from the coordinator
        # — restart vs relaunch. The bool was right and unactionable.
        try:
            lead = reg.get(name)
        except LookupError:
            return LeadStatus(False, f"{name} is not on the roster at all")
        if not lead.pane:
            return LeadStatus(False, f"{name}'s card names no pane")
        if not panes.exists(lead.pane):
            return LeadStatus(False, f"{name} is DOWN (no pane {lead.pane!r}) "
                                     f"— restart it")
        wiring = live_wiring(lead.pane, panes.cmdline)
        if wiring is None:
            # Cannot-tell still rises (see docstring), but it must not be
            # reported as "cannot drain" — that would be a claim we did not
            # measure, on the same alert that already cost credibility once.
            return LeadStatus(False, f"{name} is UP but its stop wiring could "
                                     f"NOT be read from the running process — "
                                     f"UNVERIFIED, not confirmed broken")
        if "drain" not in wiring.directions:
            carries = (f"carries {sorted(wiring.directions)}"
                       if wiring.directions else "carries no `stop_event` hook")
            whence = (f" from {wiring.settings_path}" if wiring.settings_path
                      else " and its launch line has NO --settings")
            return LeadStatus(False, f"{name} is UP but CANNOT DRAIN: it "
                                     f"{carries}{whence}. Its card says lead; "
                                     f"the process was launched before that. "
                                     f"RELAUNCH it (`st stop {name} && st new "
                                     f"{name}`) — restarting is not the fix")
        return LeadStatus(True)
    return up


def _my_shells(reg: FilesRegistry, panes, me: str) -> int | None:
    """Background shells I still own AT MY OWN STOP (aegis-q73g).

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
    """My context depth AT MY OWN STOP, in k tokens (aegis-h562).

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


def _plate_reader(root: Path):
    """A plate reader for the DEPLOYMENT'S declared backend (SHANTY_BACKEND in
    env.json/env), not a hardcoded one.

    THE BUG THIS FIXES (aegis-tisp, here in the drain). A files-only reader on a
    beads-backed fleet reads EVERY agent's plate empty — the files tracker
    (root/items) has nothing, because the work lives in beads. `st anchor` hit
    exactly this (blank status bar) and was fixed by resolving the backend; the
    drain was not, so `classify()` saw every agent as IDLE-empty-plate and the
    coordinator was told to "assign work" to agents already holding deep hauls,
    every single stop. beads.plate() already puts OPEN-ASSIGNED (haul) items on
    the plate, so reading the right backend makes a hauled agent read WORKING and
    drop out of the PRIORITIZE list on its own — the same haul-awareness the
    feed_check gate already has.

    Unknown/unset backend falls back to files (the built-in default), matching
    _backend()'s baseline; a beads repo comes from SHANTY_BEADS_REPO.
    """
    if (deployment_default(root, "SHANTY_BACKEND") or "files") == "beads":
        from .beads import (BeadsTracker, EXTRA_REPOS_KEY, parse_extra_repos,
                            plate as beads_plate)
        tracker = BeadsTracker(
            repo=deployment_default(root, "SHANTY_BEADS_REPO"),
            # aegis-qmfa1: the haul advance fires from here. A plate that cannot
            # see an embedded store reports that agent idle at every stop.
            extra_repos=parse_extra_repos(deployment_default(root, EXTRA_REPOS_KEY)))
        return lambda who: beads_plate(tracker, who)
    return lambda who: files_plate(FilesTracker(root / "items"), who)


def _plate_of(root: Path, me: str) -> tuple[str | None, str | None]:
    """What `me` held when it stopped: (item_id, status).

    Three distinct answers, and the third is why this returns a pair instead of an
    id: (None, None) = the plate was empty; (id, status) = it held that; and
    (None, "?") = THE TRACKER DID NOT ANSWER. A lookup that failed must not render
    as finished work — that is the whole aegis-mt0r lesson, and it is one `except`
    away from happening here.

    Reads the DEPLOYMENT'S backend (via _plate_reader), not files unconditionally:
    on a beads fleet a files-only read named no item for any stopped worker, so
    every stop event said "empty plate" regardless of what the worker held.
    """
    try:
        item = _plate_reader(root)(me)
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
    """The WORK assigned to `me` — trailing-segment match, the same parse
    feed_check.hauls uses (bd stores crew paths or bare names).

    A MESSAGE IS NOT WORK, and this filter is the reason the haul agrees with
    the plate instead of contradicting it. Both plate readers already exclude
    `inbox:`/`mail:` items by this exact predicate (files.plate, beads.plate) —
    the whole argument of inbox.py is that a message is a third type that must
    never occupy an agent's plate. The haul advance did NOT, so it handed the
    agent precisely the items the plate had refused.

    MEASURED 2026-07-24: the advance fed weaver aegis-atc0, titled
    `inbox: tim: ...`, with "read it and execute; close it when done". The work
    it described was billy's and already closed; there was nothing for the
    recipient to execute. The two readers disagreeing is the drift the
    _PLATE_RANK note in beads.py exists to prevent between the two BACKENDS —
    nobody had checked the haul against either of them.

    Applied to the in_progress check as well as the ready check, deliberately:
    an unread message is not an active anchor, and counting one as work-in-hand
    would silently suppress the advance for a worker that is genuinely free.
    """
    out = []
    for b in beads:
        assignee = b.get("assignee") or ""
        if (assignee.split("/")[-1] == me
                and not is_message(b.get("title", ""))
                and not is_decision(b.get("labels"))):
            out.append(b)
    return out


def _haul(reg: FilesRegistry, panes, me: str, root: Path) -> int:
    """The worker's own advance: anchor closed + assigned ready work -> BLOCK
    the stop with the next bead as the reason — the same model-reaching
    protocol drain and the Rule Zero gate already use. The coordinator is not
    involved at any point; that is the feature.

    A STOP IS A TURN BOUNDARY, NOT AN IDLE AGENT (aegis-w9z1). Claude continues
    its own turn loop, so its mid-work boundaries remain silent. Codex does not:
    an allowed stop ends the run, while the active anchor excludes it from tend's
    Rule Zero idle feed. For Codex only, an active anchor therefore blocks with a
    resume instruction. A closed anchor advances to assigned ready work as before.

    SELF-TERMINATING like feed_check: each feed claims the bead in_progress,
    so the next stop sees an active anchor and allows. The handoff branch
    blocks until the agent /clears — it terminates on the RIGHT condition
    (compliance), never a counter.

    FAIL-OPEN ABSOLUTELY: any error allows the stop, and the worker degrades
    to the tend self-feed nudge (the belt) and normal idle flow. A broken
    advance must never trap a worker at its own stop."""
    try:
        card = reg.get(me)
        from . import harness as harness_mod
        harness_name = harness_mod.name_for(card, root=root)
        # Leads hold assigned work too. Codex leads need the same explicit
        # continuation as workers; Claude leads keep their autonomous turn loop.
        if card.role != "worker" and not (card.role == "lead" and
                                           harness_name == "codex"):
            return 0
        from .feed_check import bd_cwd
        cwd = bd_cwd(reg)
        # An active anchor = mid-work turn boundary. bd list is filtered
        # client-side (same reason as feed_check: assignee formats vary).
        active = _assigned_to(me, _bd_json(["list", "--status", "in_progress", "--limit", "0"], cwd))
        resume = active[0] if (active and
                               harness_name == "codex") else None
        if active and resume is None:
            return 0
        # Keep the active Codex path dependency-free and inside the hook's
        # deadline. It is continuation of work already admitted, not a new haul
        # item, so neither the session admission ceiling nor pane handoff applies.
        if resume is not None:
            from .feed_check import haul_resume_message
            rid = resume.get("id", "?")
            if not _allow_haul_resume(root, me, rid):
                return 0
            _mark_haul_resume(root, me, rid)
            title = resume.get("title") or ""
            print(json.dumps({"decision": "block",
                              "reason": haul_resume_message(rid, title)}))
            return 0
        mine = []
        if resume is None:
            mine = _assigned_to(me, _bd_json(["ready", "--limit", "0"], cwd))
            if not mine:
                return 0

        # THE SESSION CEILING, ASKED BEFORE THE CONTEXT HANDOFF (aegis-xxae9).
        # Order matters and this is the deliberate one: the handoff is a RECYCLE
        # — it sheds context and the haul resumes — so asking it first would send
        # an over-ceiling session through /clear and straight back into the
        # queue. The ceiling is a STOP, and a stop outranks a recycle.
        #
        # Blocks ONCE per stretch, then allows: see the block-once note in
        # session_budget. Deliberately does NOT claim a bead and does NOT name
        # the next one — naming it would hand over the exact thing the ceiling
        # is withholding.
        from . import session_budget as sb
        limits, spend, ceiling = sb.gate(root, me)
        if ceiling is not None:
            if sb.already_reported(root, me, spend):
                return 0                 # told once — let the session end
            sb.mark_reported(root, me, spend)
            print(json.dumps({"decision": "block",
                              "reason": sb.stop_message(ceiling)}))
            return 0
        if limits.active and spend.signal_lost:
            # Armed but blind. Allowed — a probe bug must never stop the crew —
            # but never silently: stderr, because stdout is the block protocol.
            print(sb.signal_lost_note(limits, spend, me), file=sys.stderr)

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
        repeats = sb.times_served(root, me, nid, spend.started)
        # The item is recorded BEFORE it is served, so a feed that is interrupted
        # still counts. Under-counting is what let four items go by unremarked.
        sb.record_item(root, me, spend.session, nid)
        headroom = sb.headroom(limits, spend)
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
                          + haul_feed_message(nid, title, rest,
                                              headroom=headroom, repeats=repeats)}))
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
    # attrs=True IS LOad-BEARING (aegis-c6hli). work_state asks input_state what
    # is in the box, and input_state can only tell a dim suggestion from typed
    # text if the capture still carries the attribute. This call did not pass it,
    # so on THIS path — the one the coordinator is woken by — every pane with
    # anything in its box returned `?`, suggestion and stranded input alike.
    #
    # Measured 2026-08-01, same bytes both ways: with attrs a live ghost line
    # reads `idle` and a stranded one reads `queued`; without, both read `?`.
    # That is the whole incident this bead was filed for. billy showed `?`
    # holding an in_progress bead, its box held a SUGGESTION, and the
    # stranded-input SOP was run on it — a coordinator turn and an interrupt
    # into a working agent, spent on a phantom. `st crew` had passed attrs since
    # aegis-x6xh; this path had not, so the fleet had two classifiers' worth of
    # answers from one classifier. One capture mode, one verdict.
    screen = panes.capture(card.pane, attrs=True)
    # awaiting_answer is optional so a caller with no runtime still gets a verdict
    # — one degraded to `?`, exactly as before, rather than a crash.
    #
    # shows_ready_ui and awaiting_answer are PLAIN-TEXT matchers and the runtime
    # emits a colour run per word under -e, so a substring match silently stops
    # matching. Strip for them; keep the raw screen for work_state, which needs
    # the attribute. One capture, two views of the same instant — a second
    # capture-pane would be a different moment.
    plain = triage.strip_attrs(screen)
    awaiting = bool(awaiting_answer(plain)) if awaiting_answer else False
    return triage.work_state(screen, shows_ready_ui(plain), awaiting=awaiting)


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


def _compose_governance(events: list[StopEvent], now: float) -> str:
    """The ALERTS-ABOUT-an-agent section. Separate from the stop lines because
    these events are NOT stops (tier.is_governance), and rendering them under
    "N agent(s) stopped" would be a plain falsehood about the one fact the
    coordinator is being woken for — an agent that is very much NOT stopped.

    Latest-per-sender, same collapse as the stop lines: an agent warned three
    times about the same unhooked stretch is ONE thing to handle, not three.
    """
    if not events:
        return ""
    latest: dict[str, StopEvent] = {}
    for e in sorted(events, key=lambda x: (x.ts, x.id)):
        latest[e.frm] = e
    lines = [f"⚠ {len(latest)} agent(s) WORKING UNTRACKED — they have NOT stopped. "
             f"Each has been acting with an EMPTY HOOK (no open bead assigned) and "
             f"was warned in its own context first. Untracked work is invisible to "
             f"you and does not survive their session: give each one a bead "
             f"(`st go <id> <agent>`), or confirm the work is legitimately "
             f"untracked and say so:"]
    for name in sorted(latest):
        e = latest[name]
        rose = " (ROSE: its lead was unreachable)" if e.rose else ""
        # _age already carries its own "ago" (and "age unknown" for an unstamped
        # event, which must NOT get one appended) — do not add a second.
        lines.append(f"  - {name} — empty hook as of {_age(e.ts, now)}{rose}")
    return "\n".join(lines)


def _compose_reason(events: list[StopEvent], verdicts: dict, now: float,
                    deferred: int = 0) -> str:
    """One line per AGENT, not per event — and every line carries the three facts
    the old payload lacked: when, what it held, and whether it is free now.

    Collapsing by agent is not cosmetic. kelly emitted TWO events for one
    continuous stretch of work (turn boundaries), and two lines saying "kelly
    stopped" invite two decisions about one agent. The latest event wins; the
    count is still printed, because "this agent turned over 3 times" is itself a
    signal and hiding it would trade one wrong impression for another.

    GOVERNANCE alerts (untracked work) are split OUT to their own section first —
    they are not stops, and the header below would misdescribe them.
    """
    gov = [e for e in events if is_governance(e.reason)]
    events = [e for e in events if not is_governance(e.reason)]
    head = _compose_governance(gov, now)
    if not events:
        # Nothing but alerts. Return the section alone rather than an empty
        # "0 agent(s) stopped" header over it.
        return head
    latest: dict[str, StopEvent] = {}
    counts: dict[str, int] = {}
    for e in sorted(events, key=lambda x: (x.ts, x.id)):
        counts[e.frm] = counts.get(e.frm, 0) + 1
        latest[e.frm] = e                          # sorted -> last one wins
    lines = ([head, ""] if head else []) + [
             f"{len(latest)} agent(s) stopped — handle each (absorb / delegate / "
             f"escalate); they will NOT be redelivered. A stop is a TURN boundary, "
             f"so `now:` is the pane verdict taken just now, and it is the one to "
             f"act on:"]
    for name in sorted(latest):
        e = latest[name]
        tag = f" (ROSE: {e.reason})" if e.rose else (f" [{e.reason}]" if e.reason else "")
        # The shell count is the difference between "its turn ended" and "it is
        # finished" (aegis-q73g). Said in the destination's own words, because
        # the destination is the one about to book the item as done. Taken from
        # the LATEST event only: an earlier count is a fact about an earlier turn,
        # and re-asserting it here would report a shell that has since exited.
        if e.shells:
            tag += (f" — STILL RUNNING {e.shells} background shell(s): its TURN "
                    f"ended, its WORK may not have")
        # PAST THE CYCLE THRESHOLD (aegis-h562), from the latest event's own
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
        # BLOCKED ON A QUESTION (aegis-qxc2). The bare verdict `waiting` is already
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


# DEFER HAS A CEILING (aegis-d1qko). The defer gate below holds an event back
# while its SENDER is busy — but "busy" is measured NOW, at the coordinator's
# drain, and the event is a record of something that already happened. An agent
# that stops and immediately picks up the next item is busy at every drain that
# follows, so its stop events are deferred again and again while pending() keeps
# counting them: the coordinator's hook re-fires with the same count every turn
# and a genuinely new event hides behind the stale ones. Measured 2026-08-24:
# 9 events to sattler, all from two agents holding one in_progress item across
# repeated stops.
#
# The gate is still right — a turn boundary must not wake a coordinator for an
# agent that is mid-flight (aegis-w9z1) — it just must not be able to hold an
# event indefinitely. Past this age the event is delivered regardless of what the
# sender is doing, because at that point it is no longer a turn-boundary artifact.
#
# ts == 0 means the event predates timestamps and CANNOT be aged. Such an event
# is delivered rather than deferred: refusing forever on a measurement we cannot
# make is the bug, not the guard.
DEFER_MAX_AGE_S = float(os.environ.get("SHANTY_DEFER_MAX_AGE_S", 30 * 60))


def _drain(events: FilesEvents, me: str, reg=None, panes=None,
           shows_ready_ui=None, awaiting_answer=None, *, plate=None, rank=None,
           stood_down: bool = False, stopped=None) -> int:
    """Deliver MY events — minus the ones whose sender is still working. For an
    administrator, also append a prioritized workflow over fleet state.

    reg/panes/shows_ready_ui are optional so a caller with no pane backend still
    gets delivery (verdicts read `?`). Without them nothing is deferred: refusing
    to deliver on the strength of a check we did not run would be worse than the
    bug being fixed. plate/rank feed the admin's prioritized workflow.

    HIBERNATE IS NOT HERE. It was, briefly, and that was the bug: a gate inside
    the drain cannot see the second Stop hook that blocks the same stop. The
    decision moved to stop_policy, which weighs every rank at once.

    `stood_down` IS here, and that is not the same mistake: it never decides
    whether to block, it only stops the enrichment demanding dispatch the operator
    has already declined (#29). The events themselves are delivered either way.
    `stopped` is the same shape, per agent — a `st stop` record reader (stopped.py).
    """
    now = time.time()
    verdicts: dict[str, str] = {}
    deferred = 0
    deferred_by: dict[str, float] = {}   # sender -> oldest deferred age (s)
    overdue: list[str] = []              # senders whose events beat the ceiling
    accept = None
    if reg is not None and panes is not None and shows_ready_ui is not None:
        def accept(ev: StopEvent) -> bool:            # noqa: F811 — the wired form
            nonlocal deferred
            if is_governance(ev.reason):
                # NEVER defer an untracked-work alert. The defer gate exists to
                # stop a TURN BOUNDARY waking a coordinator for an agent that is
                # still mid-flight (aegis-w9z1) — but "that agent is mid-flight"
                # IS this alert's content. Passing it through the same gate would
                # hold back exactly the events that are true and release only the
                # ones about agents that had already stopped working untracked.
                return True
            if ev.frm not in verdicts:
                verdicts[ev.frm] = _liveness(reg, panes, shows_ready_ui, ev.frm,
                                             awaiting_answer)
            if verdicts[ev.frm] == triage.BUSY:
                # Bounded: an event older than the ceiling is delivered anyway.
                # ev.ts == 0 (pre-timestamp) is treated as ancient, not as
                # "cannot tell, so hold" — see DEFER_MAX_AGE_S above.
                age = (now - ev.ts) if ev.ts else float("inf")
                if age <= DEFER_MAX_AGE_S:
                    deferred += 1
                    deferred_by[ev.frm] = max(deferred_by.get(ev.frm, 0.0), age)
                    return False                      # DEFER — still pending
                overdue.append(ev.frm)
            return True

    got = events.drain(me, accept)                 # BLOCK-ONCE happens in drain()
    if not got:
        # Nothing to act on -> NO block -> idle. This is now also the turn-boundary
        # case: every pending sender is still mid-flight, so there is no decision
        # to make and waking the destination would be the aegis-w9z1 bug itself.
        if deferred:
            # Name the senders and the oldest age. A bare count is what made
            # aegis-d1qko read as a phantom: the operator could not tell a held
            # event from a stuck one, nor see it converging.
            who = ", ".join(f"{a} ({m/60:.0f}m)"
                            for a, m in sorted(deferred_by.items(),
                                               key=lambda kv: -kv[1]))
            print(f"stop_event: {deferred} event(s) held back — sender(s) still "
                  f"mid-flight: {who}; delivered regardless after "
                  f"{DEFER_MAX_AGE_S/60:.0f}m", file=sys.stderr)
        return 0
    reason = _compose_reason(got, verdicts, now, deferred)
    # ADMIN ENRICHMENT: only when a stop event actually fired (rides BLOCK-ONCE),
    # so a persistently-idle fleet can never re-block the admin every stop. A bare
    # _drain(events, me) is unaffected — the gate is inside _compose_workflow.
    if reg is not None and panes is not None:
        # Only STOPS feed the workflow. fold_events reads an event as "this agent
        # stopped" (it will even mint a STOPPED candidate for a sender with no
        # card), and an untracked-work alert means the opposite — folding one in
        # would put a working agent in the admin's free-to-dispatch column.
        extra = _compose_workflow(reg, panes, plate, rank,
                                  [e for e in got if not is_governance(e.reason)], me,
                                  stood_down=stood_down, stopped=stopped, now=now)
        if extra:
            reason = reason + "\n\n" + extra
    # Deliver to the MODEL via the block protocol. reason, never systemMessage.
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def _compose_workflow(reg, panes, plate, rank, events, me: str, *,
                      stood_down: bool = False, stopped=None,
                      now: float | None = None) -> str:
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
    candidates = workflow.classify(agents, panes, plate,
                                   stopped=stopped, now=now)
    candidates = workflow.fold_events(candidates, events)
    try:
        # `at_least()`, not `exact()`: a partial weighting is USABLE here — the
        # rule-based order still stands underneath it and an unweighed candidate
        # simply keeps its rule position. `exact()` would raise on the ordinary
        # case (any candidate without a mod::sym title) and degrade a haul that
        # was working. The caveat is what we surface instead (aegis-q0bzh).
        weighed = (rank or NullRanker()).weigh(candidates)
        candidates = weighed.at_least()
        note = weighed.note()
        if note:
            print(f"  note: {note}", file=sys.stderr)
    except RankUnavailable:
        pass                                      # degrade to the rule-based order
    return workflow.prioritize(candidates, stood_down=stood_down).render()


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
    plate = _plate_reader(root)   # the DEPLOYMENT's backend, not files-only (aegis-tisp)
    rank = PolicyRanker() if os.environ.get("SHANTY_RANKER") == "policy" else NullRanker()
    return _drain(events, me, reg, panes, runtime.shows_ready_ui,
                  runtime.awaiting_answer, plate=plate, rank=rank,
                  stood_down=_stood_down(root),
                  stopped=FilesStops(root / "stopped").at)


def _stood_down(root) -> bool:
    """`[fleet] stood_down`, or False if the file is missing/unreadable.

    FAILS TOWARD THE FULL LIST deliberately: a config we could not read must not
    silence the admin's workflow. The stand-down suppresses instructions, so an
    unreadable file suppressing them would be a config typo quietly turning the
    enrichment off — the failure mode #29 is about, one layer down.
    """
    from . import config
    try:
        cfg, _err = config.load_or_default(root)
        return bool(cfg.fleet.stood_down)
    except Exception:      # noqa: BLE001 — the drain must deliver regardless
        return False



if __name__ == "__main__":
    raise SystemExit(main())
