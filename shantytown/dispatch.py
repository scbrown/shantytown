"""dispatch — `st go <item> [agent]`.

The command this repo exists for. gt sling takes >120s; its --dry-run alone
takes 51s and writes nothing, because the cost is 63 sequential Dolt
connections during RESOLUTION, before any write. Underneath,
dispatch is tmux send-keys.

This module does: one registry read, one tracker read, one tracker write,
one send. That is the budget, and it is asserted in the tests.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field

from . import stores
from .attribution import attribute
from .protocols import Panes, Registry, Tracker
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
            f"({type(cause).__name__}: {str(cause)[:120]}). The agent has the work "
            f"and the tracker does not know it. Do NOT re-run `st go` — that would "
            f"deliver it twice. Record it by hand instead, then confirm with "
            f"`st anchor {agent}`.")


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
            f"Refusing to serve it to {requested} — `bd ready` already excludes it "
            f"for this reason, and dispatching it anyway puts work on a plate that "
            f"cannot be advanced. Close the blocker, or drop the dependency if it "
            f"is no longer real (`bd dep remove {item_id} <blocker>`), then dispatch."
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
            f"(`bd update {item_id} --status open`), then dispatch. If it is not, "
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
            f"(`bd update {item_id} --status open`), then dispatch."
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
        if self.note:
            # Show the note as it will actually be sent (flattened), not as it
            # was typed — a --dry-run that hides the transformation is not a
            # preview of the dispatch.
            lines.append(f"  would: carry note -> {self.note!r}")
        lines.append("  would NOT: create a convoy, spawn a session, wait for ack")
        return "\n".join(lines)


class Dispatcher:
    def __init__(self, registry: Registry, tracker: Tracker, panes: Panes,
                 governor=None, sender: str | None = None):
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
        # governor(item) -> "" | a refusal string. INJECTED, and None by default,
        # so the dispatcher keeps working with no config, no metric and no
        # Prometheus — the usage governor is a policy this module CONSULTS, never
        # one it implements. It also costs no extra reads: the gate is handed the
        # item plan() has already fetched, so the asserted budget (1 registry
        # read, 1 tracker read, 1 tracker write, 1 send) is unchanged.
        self.governor = governor

    def plan(self, item_id: str, agent_name: str, note: str | None = None,
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
        # variant of the one hole: `bd ready` already excludes an item whose
        # `blocks` dependency is open, and the dispatch path never asked. Only a
        # POSITIVE reading refuses — an empty list means "none known" (the files
        # backend has no dependency model at all), never "ready".
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
        text = attribute(text, self.sender)
        return Plan(
            item_id=item_id,
            agent=agent_name,
            pane=agent.pane,
            updates={"status": "in_progress", "assignee": agent_name},
            text=text,
            note=flat,
            store=tag,
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
           note: str | None = None, reassign: bool = False) -> Plan:
        p = self.plan(item_id, agent_name, note, reassign=reassign)
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
        try:
            self.tracker.update(item_id, **p.updates)  # 1 tracker write (last)
        except Exception as e:                         # noqa: BLE001 — any store failure
            raise DispatchedButUntracked(item_id, agent_name, p.pane, e) from e
        return p

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
