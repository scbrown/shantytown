"""dispatch — `st go <item> [agent]`.

The command this repo exists for. gt sling takes >120s; its --dry-run alone
takes 51s and writes nothing, because the cost is 63 sequential Dolt
connections during RESOLUTION, before any write. Underneath,
dispatch is tmux send-keys.

This module does: one registry read, one tracker read, one tracker write,
one tracker READ-BACK, one send. That is the budget, and it is asserted in
the tests.

The read-back is the fourth call and it was bought deliberately (aegis-8xc5w):
`st go` printed "-> in progress" while the row stayed `open` and unassigned,
INTERMITTENTLY, on the same binary and store that had worked minutes earlier.
The send had landed; only the tracker write vanished, at exit 0. A budget is a
guard against resolution churn — 63 connections to answer one question — not a
reason to report a write nobody confirmed. One extra read per dispatch is the
cheapest thing in this module and it is the only thing that can tell a
coordinator which dispatches actually took.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field

from . import stores
from .attribution import attribute
from .protocols import BLOCKER_KIND_LABELS, Panes, Registry, Tracker
from .triage import Action, Decision, triage

# Verify reads SCROLLBACK and polls briefly. Measured against a real Claude Code
# agent (harding, first live dispatches): a visible-pane one-shot check NEVER
# confirmed a delivery that plainly worked — the agent consumes the input line on
# submit and its own output scrolls the echoed id off the visible 24 lines before
# we look. So `st go` always reported could-not-tell and NEVER recorded the
# tracker update. The failure direction was safe, but the check was structurally
# incapable of SUCCEEDING against the runtime we actually use.
_VERIFY_HISTORY = 200
_VERIFY_ATTEMPTS = 5
_VERIFY_DELAY = 0.3

# The tracker write is written, READ BACK, and only re-attempted on a read-back
# that PROVES it did not land (aegis-8xc5w).
#
# This is not the blind retry the fleet forbids, and the distinction is the whole
# design. The standing rule ("commit result indeterminate" — do not retry on
# sight) governs a write whose outcome is UNKNOWN, where a retry may double-apply.
# Here the outcome is known, by reading the row: it did not change. And these two
# fields are idempotent — status=in_progress and assignee=<agent> set twice are
# the same assignment, unlike a create, which mints a second object. Verified
# absence plus an idempotent write is the one case where a retry is the correct
# move rather than the reckless one.
_TRACK_ATTEMPTS = 3
_TRACK_DELAY = 0.2


def _applied(got, want) -> bool:
    """Did the read-back row reflect what we asked the tracker to write?

    DELIBERATELY FORGIVING, and the asymmetry is the design. This predicate gates
    an exit-2 "could not tell" on a dispatch that has ALREADY been delivered to an
    agent, so a false negative here does real damage: it reports a healthy
    dispatch as broken and sends a coordinator hand-repairing a record that is
    already correct. A false POSITIVE costs us only the detection we had none of
    yesterday.

    So it answers the narrow question the fault actually poses — did the row
    CHANGE — and not "does the tracker spell things the way I do". The measured
    failure is unmistakable at this altitude: status still `open`, assignee still
    empty. A tracker that namespaces a name (`aegis/ellie` for `ellie`) has
    recorded the same assignment and must not be called a loss.
    """
    g, w = ("" if got is None else str(got)).strip(), str(want).strip()
    if not w:
        # THE CLEARING DIRECTION (aegis-ap4gm, `st repool`). A hand-back writes
        # an EMPTY assignee, so "did the row change" means the field is now
        # empty — None and "" are the same answer from different backends.
        # Without this clause an applied clear read back as a loss, earned the
        # silent-loss retries, and then raised TrackerWriteLost on a write that
        # had landed on the first attempt.
        return not g
    if not g:
        return False
    return g == w or g.split("/")[-1] == w.split("/")[-1]


def flatten_note(note: str) -> str:
    """Collapse a note to ONE line, because the transport submits on newline.

    Panes.send is `send-keys -l <text>` followed by a separate Enter. A literal
    newline INSIDE the text is not decoration — the runtime treats it as a
    submit, so a three-line note dispatches the first line and leaves the rest
    typed into a pane that has already started work. That is precisely the
    mid-flight garble triage exists to prevent, arriving through the gate rather
    than around it (aegis-8013).

    So the note is flattened here, once, on the way in: every run of whitespace
    (newlines included) becomes a single space. A caller who wants structure gets
    ' — ' separators, not line breaks. Empty/whitespace-only notes collapse to ""
    and are treated as no note at all rather than a dangling marker.
    """
    return " ".join(note.split())


class TriageRefused(Exception):
    """`st go` declined to send because the target pane is not ready to receive.

    Carries the whole Decision so the caller can print WHY and on what inputs —
    a refusal you cannot inspect is indistinguishable from a bug. Maps to exit 1.
    """

    def __init__(self, decision: Decision):
        self.decision = decision
        super().__init__(decision.why)


class DispatchedButUntracked(Exception):
    """The send LANDED and the tracker write did not (GitHub #20).

    Exit 2, and the loudest of the could-not-tells, because it is the only one
    where the fleet and the tracker actively disagree: the agent has been given
    work it will start on, and no record says so. A generic "could not tell" here
    is what left an agent idle holding an unread assignment nobody could find.

    It names the repair rather than describing the state, because the state is not
    recoverable by retrying `st go` — the work is already delivered, so a re-run
    would either refuse (already assigned) or send it twice.
    """

    def __init__(self, item_id: str, agent: str, pane: str, cause: Exception):
        self.item_id, self.agent, self.pane, self.cause = item_id, agent, pane, cause
        super().__init__(
            f"{item_id} WAS DELIVERED to {agent} ({pane}) and verified on the "
            f"pane, but the tracker write FAILED "
            # 400, not 120 (aegis-8xc5w). The cap was fine for a one-line store
            # error ("bd unreachable") and it truncated the read-back report
            # mid-word — "store still says Non" — losing exactly the half a reader
            # acts on: which fields disagree and what the row actually holds. A
            # message whose job is to be repaired by hand must not be clipped to
            # the length of the least informative failure it can carry.
            f"({type(cause).__name__}: {str(cause)[:400]}). The agent has the work "
            f"and the tracker does not know it. Do NOT re-run `st go` — that would "
            f"deliver it twice. Record it by hand instead, then confirm with "
            f"`st anchor {agent}`.")


class TrackerWriteLost(Exception):
    """The tracker write reported SUCCESS and the row did not change (aegis-8xc5w).

    Never raised to a caller on its own — go() wraps it in DispatchedButUntracked,
    because by the time we get here the send is a fact and that is the exception
    that says so. It exists as its own type so the wrapped `cause` names WHICH
    kind of tracker failure happened: a loud one (bd exited non-zero, the store
    said no) or this one, where nothing said no and nothing changed.

    That distinction is the finding. Measured 2026-08-03 against an embedded
    store: `st go` printed the dispatch, the pane received the work, and
    `bd show` returned the item still `open` with no assignee — while a direct
    `bd update` against the SAME store seconds later stuck immediately. So the
    write is ATTEMPTED (go() calls it unconditionally after a verified send) and
    it is SWALLOWED (update() raises on a non-zero bd, and nothing raised). A
    tool that reports what it intended rather than what happened is why this was
    misattributed to the store for days.
    """

    def __init__(self, item_id: str, missing: dict, attempts: int):
        self.item_id, self.missing, self.attempts = item_id, missing, attempts
        detail = ", ".join(
            f"{k}: wanted {w!r}, store still says {g!r}"
            for k, (g, w) in sorted(missing.items()))
        super().__init__(
            f"tracker reported success and the read-back disagrees after "
            f"{attempts} attempt(s) — {detail}")


class SendUnverified(Exception):
    """We sent, but reading the pane back did NOT show the work (#2).

    Maps to exit 2 (could-not-confirm), NOT exit 0. The critical consequence is
    in go(): because verify runs BEFORE the tracker write, an unverified send
    leaves the item UNTOUCHED — never marked in_progress for a send that may not
    have landed. "Send-and-assume is how you believe work was assigned when it
    wasn't" (design.md). The honest failure is "I could not confirm delivery, so
    I recorded nothing" — a human re-dispatches, rather than a tracker full of
    items nobody was told about.
    """

    def __init__(self, item_id: str, pane: str):
        self.item_id, self.pane = item_id, pane
        super().__init__(f"sent {item_id} to {pane} but could not confirm it landed")


class HasOpenBlocker(Exception):
    """The item is gated by a `blocks` dependency that is not closed.

    The THIRD variant of one hole, and the reason this class exists rather than
    a comment: plan() already refuses CLOSED and BLOCKED, and both refusals rest
    on the same sentence — serving an item writes `status=in_progress`, so a
    dispatch must not put work on a plate that nobody can advance. An item whose
    blocker is still open is exactly that, and it was the one variant left.

    THE MESSAGE STATES WHAT THIS PROCESS MEASURED, AND NOTHING ABOUT bd
    (aegis-eqhf6). It used to end "`bd ready` already excludes it for this
    reason" — an assertion about ANOTHER TOOL'S behaviour, made by code that
    never asked it. On 2026-08-04 sattler hit a case where `bd ready` DID offer a
    bead this refused, and that sentence sent the diagnosis somewhere else
    entirely: it reads as "the two agree, so your ready list must be stale",
    which is a claim st has no standing to make. The refusal now says where its
    own reading came from and names the disagreement as the finding, because a
    refusal that explains itself with someone else's guarantee is unfalsifiable
    by the person reading it.

    (`bd ready` is in fact usually right — swept 2026-08-04, 332 ready beads, 15
    carrying `blocks` edges, 14 blockers closed and correctly ready. Being
    usually right is not the point: st cannot tell, so it must not say.)

    MEASURED 2026-08-02: a P1 was dispatched to an agent while `bd ready`
    correctly EXCLUDED it, because its blocker — a filed, open telemetry gap —
    was unmet. The agent could not advance it for precisely the reason the
    tracker already knew. `bd ready` had the right answer and the dispatch path
    never asked.

    REFUSES ON A POSITIVE READING ONLY. `open_blockers` is empty for a backend
    with no dependency model, and empty means "none known", never "ready" — so
    an absent list can never manufacture a refusal, and cannot-tell stays
    cannot-tell. Same discipline as everywhere else here.

    Clearing it is deliberate: close the blocker, or drop the dependency
    (`bd dep remove <item> <blocker>`) if it is no longer real. --reassign does
    not bypass — reassign takes work from a live holder, it does not make
    blocked work workable.
    """

    def __init__(self, item_id: str, requested: str, blockers):
        self.item_id, self.requested, self.blockers = item_id, requested, tuple(blockers)
        names = ", ".join(self.blockers)
        super().__init__(
            f"{item_id} is gated by {len(self.blockers)} unmet blocker(s): {names}. "
            f"Refusing to serve it to {requested} — dispatching it puts work on a "
            f"plate that cannot be advanced. Read from this item's own `blocks` "
            f"dependencies, just now. If `br ready` offered you this item, the two "
            f"disagree and THAT is worth a bead — do not assume either is stale. "
            f"Close the blocker, or drop the dependency if it is no longer real "
            f"(`br dep remove {item_id} <blocker>`), then dispatch."
        )


class Blocked(Exception):
    """The item is BLOCKED, and dispatching it OVERWRITES that decision.

    Exactly the `Closed` shape, one status over, and it went unguarded for the
    same reason: plan() writes `status=in_progress` on serve, so serving a
    blocked bead silently converts "nobody should work this yet" into "somebody
    is working this now". Blocking is a DELIBERATE act — an operator decided the
    thing cannot be advanced — and a dispatch must not undo it as a side effect.

    MEASURED 2026-08-02, on the author of the fix. I set a P1 bead to blocked
    with its gate written on the bead, verified `bd show` reported BLOCKED, and a
    dispatch then served it back to me as in_progress. The status I had set was
    gone, the bead was on my plate, and the `is_blocked` plate-reader exclusion I
    had shipped hours earlier could not help — by the time the plate read it, it
    was no longer blocked.

    That is the second-order failure worth naming: this defect DEFEATS the
    plate-reader exclusion entirely. Excluding blocked from plates is worthless
    if the dispatch path launders the status on the way in. The same reasoning as `Closed`'s
    "reopening is a separate, deliberate act": unblocking is
    `bd update <id> --status open`, never a dispatch side effect, and
    --reassign does not bypass it — reassign takes work from a live holder, it
    does not overrule a block.
    """

    def __init__(self, item_id: str, requested: str):
        self.item_id, self.requested = item_id, requested
        super().__init__(
            f"{item_id} is BLOCKED; refusing to serve it to {requested}. Serving "
            f"it would overwrite that status with in_progress and put an item "
            f"nobody can advance on an agent's plate — which is what cycles "
            f"agents. If the block is resolved, clear it deliberately "
            f"(`br update {item_id} --status open`), then dispatch. If it is not, "
            f"the bead's own comment says what it is waiting for."
        )


class Closed(Exception):
    """The item is CLOSED, and closed is terminal for the serve path (aegis-vuh33).

    Maps to exit 1 like every other plan() refusal: nothing written, nothing sent.
    Serving a closed bead used to sail through — the steal-guard's own
    `status != "closed"` clause waved it past, and plan() then wrote
    status=in_progress, RESURRECTING finished work. Measured 2026-07-24: aegis-rqbs
    / 5vgss / i9ish reverted closed->in_progress at serve time, and the revert was
    indistinguishable from `bd close` silently losing a write — arnold recorded that
    false data-plane conclusion in durable memory before dolt_history disproved it.

    Reopening is a SEPARATE, DELIBERATE act (`bd update --status open`), never a
    silent side effect of a dispatch. --reassign does not bypass this: reassign
    takes work from a live holder; it does not raise the dead."""

    def __init__(self, item_id: str, requested: str):
        self.item_id, self.requested = item_id, requested
        super().__init__(
            f"{item_id} is CLOSED; refusing to serve it to {requested}. Closed is "
            f"terminal — serving it would revert it to in_progress and re-do "
            f"finished work. If it must be worked again, reopen it deliberately "
            f"(`br update {item_id} --status open`), then dispatch."
        )


class GovernorRefused(Exception):
    """The usage governor's current tier does not admit this dispatch (aegis-hdqej).

    Maps to exit 1 like every other plan() refusal: nothing written, nothing sent.
    It belongs HERE, beside Closed and AlreadyAssigned, for one reason — those are
    the refusals that happen after the item is read and before anything is
    composed, and the governor needs exactly the same position: it must see the
    item's PRIORITY (so it cannot run earlier) and it must stop the dispatch
    before a single write (so it cannot run later).

    The message is the governor's own, verbatim, because it names the tier AND
    the reading that engaged it. A refusal an operator cannot trace back to a
    number is indistinguishable from a bug — the same argument TriageRefused
    makes for carrying the whole Decision.
    """


class AlreadyAssigned(Exception):
    """The item is already held by a DIFFERENT agent. Refuse rather than steal.

    Maps to exit 1 (precondition failure) like every other plan() refusal: nothing
    is written and nothing is sent. Carries both names so the operator can see who
    holds it and decide — reassignment is a real operation, it just has to be
    deliberate (`--reassign`) rather than a silent side effect of dispatching.
    """

    def __init__(self, item_id: str, holder: str, requested: str):
        self.item_id, self.holder, self.requested = item_id, holder, requested
        super().__init__(
            f"{item_id} is already assigned to {holder}; refusing to reassign it to "
            f"{requested}. Re-dispatch to {holder} to re-nudge, or pass --reassign "
            f"to take it deliberately."
        )


class RepoolRefused(Exception):
    """Repool hands WORKABLE work back to the pool — it neither revives a
    terminal state nor clears a decision. Maps to exit 1: nothing written.

    Same two refusals as the serve path, arrived at from the other side:
    repooling a CLOSED item would resurrect finished work, and repooling a
    BLOCKED item would put "nobody should work this yet" onto `bd ready` as
    though it were workable. Both are deliberate acts if they are ever right,
    never a hand-back side effect.
    """


class DeferRefused(Exception):
    """A structured defer was invalid or unsupported; nothing was written."""


@dataclass
class Repool:
    """What a repool did (or would do). `noop` = already open and unassigned."""
    item_id: str
    holder: str = ""          # who had it; "" = nobody (the orphan case)
    was_status: str = ""
    noop: bool = False
    track_attempts: int = 0   # 0 on --dry-run and on noop


@dataclass
class Deferred:
    """What a structured defer did (or would do)."""
    item_id: str
    kind: str
    label: str
    reason: str
    was_status: str = ""
    noop: bool = False
    track_attempts: int = 0


@dataclass
class Plan:
    """What a dispatch WOULD do. --dry-run returns this and stops."""
    item_id: str
    agent: str
    pane: str
    updates: dict = field(default_factory=dict)
    text: str = ""
    note: str = ""
    store: str = ""   # the `[st store: ...]` tag riding the payload (aegis-81zyb)
    quipu_nodes: list = field(default_factory=list)  # graph context (aegis-x6yoq)
    sender: str = ""  # who the payload is signed as, "" = unsigned (aegis-5vxmz)
    serve_id: str = ""
    unreadable_deps: int = 0   # dependency rows the tracker counted but could
                               # not resolve, so we cannot tell if they block
                               # (aegis-kt7jr)
    orphaned_in_progress: bool = False
    # This item was in_progress with NO assignee when we picked it up
    # (aegis-ap4gm). Not a refusal — dispatching it is the repair — but the
    # receiving agent is resuming somebody's abandoned work and the payload
    # otherwise reads like a fresh start.
    track_attempts: int = 0    # how many write+read-back rounds the tracker
                               # update actually needed. 0 on --dry-run (nothing
                               # was written), 1 on a healthy dispatch, >1 when a
                               # write reported success and did NOT land
                               # (aegis-8xc5w). COUNTED, not merely survived: an
                               # intermittent fault that is silently retried is
                               # an intermittent fault nobody can ever root-cause,
                               # and this is the only place in the fleet that sees
                               # one happen.

    def render(self) -> str:
        lines = [
            f"  would: tracker.update({self.item_id}, "
            + ", ".join(f"{k}={v}" for k, v in self.updates.items())
            + ")",
            f"  would: send-keys -> pane {self.pane}",
        ]
        if self.store:
            # WHERE, in the preview (aegis-81zyb). The operator reading --dry-run
            # is deciding whether this dispatch is right, and "is it pointing at
            # the store I think it is" is part of that question — on a host with
            # 125 of them it is most of it. render() shows the note but never the
            # payload, so a store carried only in `text` would reach the agent and
            # not the person authorising the send.
            lines.append(f"  would: name store -> {self.store}")
        if self.quipu_nodes:
            # Shown for the same reason as the store line: render() prints the
            # note but never the payload, so context carried only in `text`
            # reaches the AGENT and not the person authorising the send.
            lines.append(f"  would: carry graph context -> {', '.join(self.quipu_nodes)}")
        if self.note:
            # Show the note as it will actually be sent (flattened), not as it
            # was typed — a --dry-run that hides the transformation is not a
            # preview of the dispatch.
            lines.append(f"  would: carry note -> {self.note!r}")
        # WHO IT WILL BE SIGNED AS (aegis-5vxmz). Same argument as the store line
        # above, and the same workaround for the same fact: render() shows the
        # note but never the payload, so an attribution carried only in `text`
        # reaches the AGENT and not the person authorising the send.
        #
        # PRINTED IN BOTH DIRECTIONS, and the unsigned one is the direction that
        # matters. send-keys types at the same prompt the human uses, so an
        # unsigned dispatch does not arrive anonymous — it arrives looking like
        # the OPERATOR handing out work. An operator reading a preview that went
        # quiet on the subject would have no way to know that is what they were
        # about to authorise, and silence would read as "nothing to report".
        lines.append(
            f"  would: sign as -> {self.sender}" if self.sender else
            "  would: sign as -> NOBODY — this dispatch arrives UNSIGNED, which "
            "reads as the operator (set $SHANTY_AGENT)")
        if self.orphaned_in_progress:
            lines.append(
                "  would: ⚠ RESUME an ORPHAN — this item is in_progress with NO "
                "assignee, i.e. started and handed back. The assignee guard does "
                "not fire (it keys on a field that is empty), so nothing else "
                "would tell you.")
        if self.unreadable_deps:
            # SAY WHAT WE COULD NOT SEE (aegis-kt7jr). The blocker check ran and
            # was INCOMPLETE, which is a third answer beside "clear" and
            # "blocked" — and the only place it can be reported, because every bd
            # dependency view drops these rows silently and `bd dep tree` prints
            # [READY] over them. Named in the preview an operator reads to decide
            # the dispatch is right, for the same reason the store tag is.
            lines.append(
                f"  would: ⚠ {self.unreadable_deps} dependency row(s) UNREADABLE "
                f"— counted by the tracker, resolvable in no store reachable from "
                f"here. If one of them is a `blocks` edge, this item is NOT ready "
                f"and nothing can tell you so (`bd show {self.item_id} --json`: "
                f"compare dependency_count against dependencies)")
        lines.append("  would NOT: create a convoy, spawn a session, wait for ack")
        return "\n".join(lines)


class Dispatcher:
    def __init__(self, registry: Registry, tracker: Tracker, panes: Panes,
                 governor=None, sender: str | None = None, audit=None):
        self.registry = registry
        self.tracker = tracker
        self.panes = panes
        # WHO IS DISPATCHING (aegis-5vxmz). None means "could not establish", and
        # that stays BARE — see attribution.attribute. Injected rather than read
        # from the environment here because a Dispatcher constructed in a test or
        # by a future caller must not silently inherit whatever $SHANTY_AGENT the
        # surrounding process happens to carry: the sender is a fact the CALLER
        # knows, and a transport that guesses it is the failure this prevents.
        self.sender = sender
        self._new_serve = audit.new_serve if audit is not None else None
        self._audit_record = audit.record if audit is not None else None
        # governor(item) -> "" | a refusal string. INJECTED, and None by default,
        # so the dispatcher keeps working with no config, no metric and no
        # Prometheus — the usage governor is a policy this module CONSULTS, never
        # one it implements. It also costs no extra reads: the gate is handed the
        # item plan() has already fetched, so the asserted budget (1 registry
        # read, 1 tracker read, 1 tracker write, 1 send) is unchanged.
        self.governor = governor

    def plan(self, item_id: str, agent_name: str, note: str | None = None,
             quipu_nodes: list | None = None,
             reassign: bool = False) -> Plan:
        """Resolve only. No writes. This is what --dry-run shows.

        Every refusal here is a precondition failure -> exit 1, and it happens
        BEFORE anything is written. Refusing loudly beats a half-dispatch.

        `note` is a caveat that must ride WITH the work (aegis-8013): it is
        composed into the same payload, so it goes through the same triage gate
        and the same verify. The dispatch and its qualifier are delivered
        together or refused together — a caveat that arrives separately can
        arrive after the worker has already acted on the uncaveated work.
        """
        agent = self.registry.get(agent_name)          # 1 registry read
        if agent.pane is None:
            raise LookupError(f"{agent_name} has no pane in the registry")
        if not self.panes.exists(agent.pane):
            raise LookupError(f"pane {agent.pane} for {agent_name} does not exist")
        item = self.tracker.get(item_id)               # 1 tracker read
        # CLOSED IS TERMINAL (aegis-vuh33). Refuse BEFORE the holder logic and
        # before composing the payload — a closed bead served is a closed bead
        # reverted to in_progress, which re-does finished work AND forges a
        # phantom "bd close lost my write" bug report. Reopening is deliberate,
        # never a dispatch side effect; --reassign does not raise the dead.
        if item.status == "closed":
            raise Closed(item_id, agent_name)
        # BLOCKED IS A DECISION, AND SERVING IT OVERWRITES THE DECISION
        # (internal-ref). Same position and same argument as CLOSED directly
        # above: plan() writes status=in_progress, so a dispatch silently turns
        # "nobody should work this yet" into "somebody is working it now" and
        # puts an unadvanceable item on a plate — which is precisely what cycles
        # agents. Checked here, before the holder logic and before composing the
        # payload, so a refusal does no work at all.
        if item.status == "blocked":
            raise Blocked(item_id, agent_name)
        # ...AND AN UNMET BLOCKER IS THE SAME REFUSAL (internal-ref). Third
        # variant of the one hole: an item whose `blocks` dependency is open
        # cannot be advanced, and the dispatch path never asked. Only a POSITIVE
        # reading refuses — an empty list means "none known" (the files backend
        # has no dependency model at all), never "ready".
        #
        # This used to justify itself with "`bd ready` already excludes" such an
        # item. Dropped, not softened (aegis-eqhf6): the refusal is correct on
        # its OWN reading of this item's dependencies, and borrowing another
        # tool's guarantee for it made the check look redundant with bd rather
        # than independent of it — which is the opposite of why it exists.
        if getattr(item, "open_blockers", ()):
            raise HasOpenBlocker(item_id, agent_name, item.open_blockers)
        # Do not STEAL work someone is already doing (aegis-uvw5, the 7yeb shape).
        # plan() used to read the item and overwrite status/assignee unconditionally,
        # so dispatching an item another agent held silently reassigned it and two
        # agents worked it in parallel. Measured 2026-07-19: two agents investigated
        # uvw5 five minutes apart, ran the same commands, and reached the same wall —
        # duplicated effort that no tool ever flagged.
        # Re-dispatching to the SAME holder stays allowed: that is a re-nudge, not a
        # steal, and it is how you recover a dropped send.
        # Checked BEFORE composing the payload: a refusal should do no work at all.
        # (No `status != "closed"` guard here anymore — a closed item can never
        # reach this line, so a clause implying it could would be a lie.)
        holder = (item.assignee or "").strip()
        if not reassign and holder and holder != agent_name:
            raise AlreadyAssigned(item_id, holder, agent_name)
        # THE GUARD ABOVE IS NOT WEAK HERE — IT IS BYPASSED (aegis-ap4gm, sattler).
        # `and holder` means an EMPTY assignee never conflicts, so an item that is
        # in_progress with NOBODY on it sails through silently. That state is not
        # ordinary: something STARTED this and it was re-pooled or abandoned
        # (`bd update -a ""` clears the assignee and leaves the status), so the
        # next agent is picking up somebody's half-done work while the payload
        # reads like fresh work.
        #
        # Measured tonight: an unassigned in_progress bead was dispatched, `st go`
        # accepted SILENTLY, and it ended safely only because the receiving agent
        # refused it on its premise. No mechanism caught it.
        #
        # CARRIED, NOT RAISED — the same trade `unreadable_deps` makes below, and
        # for the same reason: dispatching it is the REPAIR (it acquires an owner),
        # so refusing would block the remedy. What was missing was not a refusal,
        # it was the fact reaching a human and the receiving agent.
        orphaned = (item.status or "").strip() == "in_progress" and not holder
        # THE USAGE GOVERNOR (aegis-hdqej), last of the precondition refusals and
        # deliberately last: a CLOSED or STOLEN item is wrong to dispatch at any
        # usage level, and reporting "the 70% tier refused this" about a bead that
        # is closed would send the operator to the wrong fix. The governor gets
        # the final word on work that is otherwise dispatchable, and only that.
        # --reassign does NOT bypass it: reassignment is still a dispatch, and the
        # tier is about what the fleet may SPEND, not about who holds what.
        # The AGENT is passed, not just the item, because a floor exemption is a
        # property of WHO is being dispatched to (aegis-yegfx). The gate stays a
        # callable returning "" | refusal; it simply now knows both halves of the
        # question it is being asked.
        if self.governor is not None:
            refusal = self.governor(item, agent_name)
            if refusal:
                raise GovernorRefused(refusal)
        text = f"Work is on your hook: {item_id} — {item.title}"
        # NAME THE STORE (aegis-81zyb). An id and a title are not a dispatch on a
        # host with 125 bd stores — they are a riddle, and the receiving agent has
        # no signal that the question is even open. The tag rides HERE, inside
        # plan(), rather than being appended by the caller like keep-current's is,
        # for the reason the note rides here: it must go through the same triage
        # gate and the same verify, and it must show in --dry-run. A store that is
        # only named on the real run is not named in the preview an operator reads
        # to decide whether the dispatch is right.
        #
        # This costs NO extra reads: self.tracker was constructed against that
        # store and agent.workspace came off the registry read at the top.
        tag = stores.hook_tag(self.tracker, agent.workspace) or ""
        if tag:
            text += f" — {tag}"
        # GRAPH CONTEXT (aegis-x6yoq, Stiwi: "and when assigning work too").
        # Named nodes ride the payload so the receiving agent starts from what the
        # graph already knows instead of re-deriving it — query-first, mechanized
        # at the moment work is handed over. Placed with the note, and for the same
        # reason: it must pass the same triage gate, the same verify, and show in
        # --dry-run. A hint that only appears on the real run is absent from the
        # preview the operator uses to decide the dispatch is right.
        nodes = [n.strip() for n in (quipu_nodes or []) if n and n.strip()]
        if nodes:
            text += f" — GRAPH: {', '.join(nodes)} (quipu_search these first)"
        flat = flatten_note(note) if note else ""
        if flat:
            # The note goes AFTER the id and title on purpose: verify() looks for
            # the item id in the pane, and a long note must not push it out of
            # what we can read back.
            text += f" — NOTE: {flat}"
        # ATTRIBUTE LAST, so the prefix leads the line (aegis-5vxmz), and HERE in
        # plan() rather than at the send for the same reason the store tag rides
        # here: it must go through the same triage gate, the same verify, and it
        # must show in --dry-run. An attribution that only appears on the real run
        # is absent from the preview an operator reads to decide the dispatch is
        # right — and this is the payload most worth signing, because it is the
        # one that tells an agent to START WORKING.
        #
        # MEASURED SAFE, not assumed (2026-08-04): nothing parses this text.
        # `triage.triage(panes, target, new_work)` accepts new_work and never
        # reads it (only `unrelated()` does, and triage does not call it);
        # `verify` greps the pane for the ITEM ID, which the prefix does not
        # touch; and a grep of ~/.gt and ~/.claude found "on your hook" only in
        # primer PROSE, no matcher. The bead flagged this contract as the reason
        # to stop — it holds, and the check is written down so the next person
        # does not have to re-derive it.
        # THE RECEIVING AGENT IS THE ONE WHO CANNOT OTHERWISE TELL (aegis-ap4gm).
        # An orphaned in_progress item arrives looking exactly like fresh work.
        # Appended BEFORE attribution so the signature stays last.
        if orphaned:
            text += (" — NOTE: this item was already in_progress with NO assignee: "
                     "you are RESUMING work somebody started and handed back, not "
                     "starting it. Read its comments before acting.")
        serve_id = self._new_serve() if self._new_serve is not None else ""
        text = attribute(text, self.sender)
        if serve_id:
            text += f" — [st serve:{serve_id} worker:{agent_name}]"
        return Plan(
            item_id=item_id,
            agent=agent_name,
            pane=agent.pane,
            updates={"status": "in_progress", "assignee": agent_name},
            text=text,
            note=flat,
            store=tag,
            quipu_nodes=nodes,
            sender=self.sender or "",
            serve_id=serve_id,
            # Carried, NEVER raised (aegis-kt7jr). We cannot know the dropped
            # rows' type, so refusing here would refuse work over what might be a
            # `relates-to` link — the exact "cannot-tell manufacturing a refusal"
            # this module's blocker guard commits against in its own docstring.
            # Reported instead, so the incompleteness is visible to the human who
            # CAN go look. If that trade is ever re-ruled, this is the line.
            unreadable_deps=int(getattr(item, "unreadable_deps", 0) or 0),
            orphaned_in_progress=orphaned,
        )

    def triage(self, item_id: str, agent_name: str, note: str | None = None) -> Decision:
        """What st go WOULD do to that pane, without touching it. Read-only.

        Closes shantytown #1: st go sent into mid-flight panes. It went straight
        to send-keys, so dispatching to an agent that was mid-response
        interrupted its work. Now go() consults sentinel's triage first and only
        NUDGE proceeds. This method exposes that judgement for --dry-run and for
        `st go` to print before it refuses.
        """
        p = self.plan(item_id, agent_name, note)  # resolve + precondition-check
        return triage(self.panes, p.pane, p.text)

    def go(self, item_id: str, agent_name: str, dry_run: bool = False,
           note: str | None = None, reassign: bool = False,
           quipu_nodes: list | None = None) -> Plan:
        p = self.plan(item_id, agent_name, note, quipu_nodes=quipu_nodes,
                      reassign=reassign)
        if dry_run:
            return p
        # #1: consult triage BEFORE any write. A REFUSE/CLEAR/RESTART here means
        # we never mark the item in_progress and never send — no half-dispatch,
        # no interrupted agent. Only a healthy pane (NUDGE) proceeds.
        decision = triage(self.panes, p.pane, p.text)
        if decision.action is not Action.NUDGE:
            raise TriageRefused(decision)
        # #2: SEND -> VERIFY -> UPDATE, in that order, on purpose. The tracker
        # write moved AFTER a confirmed send so a dropped send never marks work
        # in_progress. verify reads the pane back for the item id — the thing we
        # just sent must now be visible on the pane. If it is not, we sent into
        # the void: raise SendUnverified (exit 2) and write NOTHING.
        self.panes.send(p.pane, p.text)                # 1 send
        if not self.verify(p.pane, item_id):
            raise SendUnverified(item_id, p.pane)
        # THE OTHER HALF OF THE WINDOW (GitHub #20). send-then-update is the right
        # order — a dropped send must never mark work in_progress — but it leaves
        # the mirror failure: the agent HAS the work (verified on its pane) and the
        # tracker write then fails, so nothing in the system knows the item was
        # assigned and the agent sits holding it. That cannot be eliminated without
        # a transaction across a pane and a tracker, which we do not have. It CAN
        # stop being reported as a generic "could not tell": the send is a FACT by
        # this point, and the operator needs to know it landed and what to repair.
        #
        # AND THE WRITE IS READ BACK (aegis-8xc5w). `except Exception` below only
        # ever caught a tracker that SAID it failed. The measured fault says
        # nothing: bd exits 0, the row does not change, and this function then
        # returns a Plan that the CLI prints as "-> in progress". The item stays
        # open and unassigned, re-enters `bd ready`, and is dispatched again to
        # somebody else — the duplicate-work failure the assignee guard exists to
        # prevent, arriving underneath it. Verify by reading the row, never by the
        # tool's success message.
        try:
            p.track_attempts = self._track(item_id, p.updates)
        except Exception as e:                         # noqa: BLE001 — any store failure
            raise DispatchedButUntracked(item_id, agent_name, p.pane, e) from e
        if self._audit_record is not None:
            window_id = f"dispatch-{p.serve_id}"
            self._audit_record(window_id, leg="claim", worker=agent_name,
                               item=item_id, attempted=True, acted_on=True,
                               serve_id=p.serve_id, state="claim_committed",
                               reason="tracker write read back applied")
            self._audit_record(window_id, leg="delivery", worker=agent_name,
                               item=item_id, attempted=True, acted_on=True,
                               serve_id=p.serve_id, state="input_sent",
                               reason="pane send verified before tracker update")
        return p

    def _track(self, item_id: str, updates: dict) -> int:
        """Write the tracker, READ IT BACK, and return the attempt that stuck.

        Raises TrackerWriteLost if the row still does not reflect the dispatch
        after _TRACK_ATTEMPTS. go() turns that into DispatchedButUntracked, which
        is the honest report: the agent HAS the work and no record says so.

        A raised `tracker.update` is NOT retried and never has been — it
        propagates on the spot. A loud failure has already told us something
        (unknown id, store unreachable) and re-running it just asks the same
        question again. Only the SILENT loss — exit 0, row unchanged — earns a
        second attempt, and only because the read-back proved there is nothing to
        double-apply. See _TRACK_ATTEMPTS for why that is not the blind retry the
        fleet forbids.
        """
        missing: dict = {}
        for attempt in range(1, _TRACK_ATTEMPTS + 1):
            if attempt > 1:
                # Reached ONLY on a verified loss. If the fault is contention
                # with something else touching the same working set, the pause is
                # the point; if it is not, three attempts cost 0.4s and we find
                # out from the counter rather than from a coordinator's guess.
                time.sleep(_TRACK_DELAY)
            self.tracker.update(item_id, **updates)
            item = self.tracker.get(item_id)
            missing = {
                k: (getattr(item, k, None), v)
                for k, v in updates.items()
                if not _applied(getattr(item, k, None), v)
            }
            if not missing:
                return attempt
        raise TrackerWriteLost(item_id, missing, _TRACK_ATTEMPTS)

    def repool(self, item_id: str, dry_run: bool = False) -> Repool:
        """Hand an item back to the pool so it RETURNS TO THE BOARD (aegis-ap4gm #1).

        The documented hand-back (`bd update -a ""`) clears the assignee and
        LEAVES the status at in_progress. The item is then in no haul, on no
        plate, and outside `bd ready` — the agent did everything right and the
        work silently left the system; that is the mechanism behind every
        unassigned-in_progress orphan measured on that bead. Repool is the whole
        hand-back in one verified write: status -> open AND assignee cleared,
        read back like a dispatch write, because the fault this class keeps
        producing is precisely a write that reported success and did not land.
        """
        item = self.tracker.get(item_id)               # 1 tracker read
        if item.status == "closed":
            raise RepoolRefused(
                f"{item_id} is closed — closed is terminal and there is nothing "
                f"to hand back. Reopening is a deliberate act "
                f"(`br update {item_id} --status open`), never a repool side effect.")
        if item.status == "blocked":
            raise RepoolRefused(
                f"{item_id} is blocked — blocked is a decision, and repooling it "
                f"would serve that decision to the next free agent via `br ready`. "
                f"Clear the blocker deliberately first, then repool.")
        holder = (item.assignee or "").strip()
        if item.status == "open" and not holder:
            return Repool(item_id, noop=True)
        r = Repool(item_id, holder=holder, was_status=item.status)
        if dry_run:
            return r
        r.track_attempts = self._track(item_id,
                                       {"status": "open", "assignee": ""})
        return r

    def defer(self, item_id: str, kind: str, reason: str,
              dry_run: bool = False) -> Deferred:
        """Park an item with one explicit blocker kind and a durable reason.

        Classification is supplied by the deferrer, never inferred from prose.
        The tracker primitive must write status, the selected label, removal of
        contradictory labels, and the reason together; read-back proves both
        structured outcomes before this reports success.
        """
        label = BLOCKER_KIND_LABELS.get(kind)
        if label is None:
            raise DeferRefused(
                f"unknown blocker kind {kind!r}; choose "
                f"{', '.join(BLOCKER_KIND_LABELS)}")
        reason = reason.strip()
        if not reason:
            raise DeferRefused(
                "a defer reason is required — name the referent and the "
                "condition that should be re-tested")
        item = self.tracker.get(item_id)
        if item.status == "closed":
            raise DeferRefused(
                f"{item_id} is closed — deferring it would resurrect terminal work")
        if not getattr(self.tracker, "_structured_defer", False):
            raise DeferRefused(
                "this tracker backend cannot atomically record structured deferrals")
        if item.status == "deferred" and item.blocker_kind == label:
            return Deferred(item_id, kind, label, reason,
                            was_status=item.status, noop=True)
        result = Deferred(item_id, kind, label, reason, was_status=item.status)
        if dry_run:
            return result
        missing = {}
        for attempt in range(1, _TRACK_ATTEMPTS + 1):
            if attempt > 1:
                time.sleep(_TRACK_DELAY)
            self.tracker.update(item_id, status="deferred",
                                blocker_kind=label, defer_reason=reason)
            current = self.tracker.get(item_id)
            missing = {}
            if current.status != "deferred":
                missing["status"] = (current.status, "deferred")
            if current.blocker_kind != label:
                missing["blocker_kind"] = (current.blocker_kind, label)
            if not missing:
                result.track_attempts = attempt
                return result
        raise TrackerWriteLost(item_id, missing, _TRACK_ATTEMPTS)

    def verify(self, pane: str, item_id: str) -> bool:
        """Did the send land? Read the pane back and look for the item id.

        design.md: "verify reads the pane back. Send-and-assume is how you
        believe work was assigned when it wasn't." A false negative (the agent
        cleared it before we looked) is SAFE by construction: it maps to exit 2
        and leaves the tracker untouched, so a human re-dispatches rather than
        the tracker lying — never the other direction.

        But safe-by-construction is not an excuse for a check that can only ever
        fail. Reading the VISIBLE pane once never confirmed a real delivery to a
        Claude Code agent (see the constants above), so this reads SCROLLBACK and
        polls: the echoed id survives in history even after the agent's own
        output pushes it off-screen. Still one-directional — we only ever return
        True on positive evidence that the id reached the pane.
        """
        for attempt in range(_VERIFY_ATTEMPTS):
            if item_id in self.panes.capture(pane, history=_VERIFY_HISTORY):
                return True
            if attempt + 1 < _VERIFY_ATTEMPTS:
                time.sleep(_VERIFY_DELAY)
        return False
