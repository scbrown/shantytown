"""triage — the part worth packaging. Everything else is plumbing.

Every rule here is encoded knowledge that was paid for. The comments say who paid.

The design constraint that outranks accuracy: DO NOT SHIP A CONFIDENT HEURISTIC
YOU CANNOT INSPECT. Every decision carries its inputs, so an operator can see why
it chose. `context_high` and `unrelated` are honest unknowns — crude and visible
beats clever and opaque.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from enum import Enum


class Action(Enum):
    NUDGE = "nudge"       # healthy — send it
    REFUSE = "refuse"     # in-flight work. Sending would interrupt it.
    CLEAR = "clear"       # high context, unrelated — clear before sending
    RESTART = "restart"   # no session, or wedged. LAUNCHER-relaunch, never handoff.


@dataclass
class Decision:
    action: Action
    why: str
    inputs: dict = field(default_factory=dict)   # the whole point: inspectable

    def render(self) -> str:
        ins = " ".join(f"{k}={v!r}" for k, v in sorted(self.inputs.items()))
        return f"{self.action.value.upper():8} {self.why}\n         inputs: {ins}"


# --- the honest unknowns. Crude, visible, tunable. -------------------------

# A wedge is the SESSION being dead, not the agent printing something ugly.
# "Traceback (most recent call last)" was removed 2026-07-16: agents
# print tracebacks constantly — running a failing test prints one — and RESTART
# means LAUNCHER-RELAUNCH. MEASURED: a healthy, idle agent whose pane showed a
# ZeroDivisionError traceback and then "I'll fix that now" was classified
# RESTART/wedged. That kills a working agent for doing its job, which is far
# worse than missing a wedge. The remaining markers mean the process itself is
# gone, not that the agent had a bad day.
WEDGED_MARKERS = ("[Process completed]", "^C^C")
INFLIGHT_MARKERS = ("esc to interrupt", "Running…", "Running...", "tokens · esc")

# Chrome lives at the bottom. Only look there: scrollback mentioning a marker is
# an agent TALKING about a state, not being in it — and this repo's own source
# contains every one of these strings.
_TAIL_LINES = 8

# --- attributes: the one bit `capture-pane -p` throws away (internal-ref) ------
#
# MEASURED across 18 live panes, 2026-07-20, `capture-pane -p -e`:
#
#   placeholder   \x1b[39m❯\xa0\x1b[2mbd ready — pick the next item\x1b[0m
#   real input    \x1b[38;5;246m❯\xa0\x1b[39mzzPROBEzz
#   empty         \x1b[38;5;246m❯\xa0\x1b[39m
#
# SGR 2 (dim) wraps a placeholder and NOTHING else; real typed input carries no
# SGR at all. Without -e all three collapse to `❯ …`, and the first two become
# the same bytes — which is how an administrator read a healthy idle agent as a
# stalled dispatch and typed into its buffer to "fix" it, and how a REAL stall
# (text delivered by send-keys that never submitted) reads as "just a
# suggestion". Ambiguous in both directions, on the tier's only liveness oracle.
#
# So: capture WITH attributes, judge the input box on the attribute, and strip
# for every other predicate. Stripping is not optional — with -e the runtime
# emits a colour run PER WORD, so "esc to interrupt" arrives as
# `\x1b[38;5;246mesc\x1b[39m \x1b[38;5;246mto\x1b[39m …` and a substring match
# for the marker silently stops matching. One capture, two views of the same
# instant: a second capture-pane would be a different moment.
_ANSI = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"          # CSI (colour, cursor, …)
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC (hyperlinks: \x1b]8;;URL\x1b\)
    r"|\x1b[@-Z\\-_]"                      # lone two-byte escapes
)
_DIM = "\x1b[2m"
# ASCII `>` as well as `❯`: the runtime renders ❯, the tests and older panes `>`.
_PROMPT_GLYPHS = ("❯", ">")


def strip_attrs(screen: str) -> str:
    """The plain-text view of an attribute-carrying capture.

    Exported because callers OUTSIDE this module hand the same screen to
    runtime.shows_ready_ui, whose markers are plain substrings and break on a
    per-word colour run exactly like ours do.
    """
    return _ANSI.sub("", screen)


def _tail(screen: str, n: int = _TAIL_LINES) -> str:
    # Strips: every text predicate below judges CONTENT, and content is what is
    # left when the attributes come off. Doing it here means a screen captured
    # with -e and one captured without are the same input to all of them.
    #
    # TRAILING BLANK ROWS ARE DROPPED FIRST (internal-ref). tmux pads a capture to
    # the pane height, so a UI sitting N rows above the bottom arrives with N
    # blank lines under it — and a fixed window off the raw bottom then spends
    # itself on padding. kelly's answered picker hid behind five blank rows
    # (internal-ref); twelve stranded-input panes hid behind seven and printed
    # `idle` (internal-ref). awaiting_answer and auth_dead already trimmed for
    # exactly this reason; the predicates on THIS window were still blind.
    # Padding is not content, so it does not get to spend the window.
    lines = strip_attrs(screen).splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines[-n:])


def looks_wedged(screen: str) -> bool:
    return any(m in _tail(screen) for m in WEDGED_MARKERS)


def mid_flight(screen: str) -> bool:
    """An agent actively working. Sending now interrupts it.

    Gas Town's own nudge help says --mode immediate 'Send directly via tmux
    send-keys' and warns it interrupts. REFUSE is a real outcome.

    Tail-only, same reason as looks_wedged: "esc to interrupt" appears in this
    very file, so an agent reading triage.py must not read as permanently busy.
    """
    return any(m in _tail(screen) for m in INFLIGHT_MARKERS)


# --- background shells: work that outlives the turn -------------------------

# Claude Code reports background shells in TWO places, and both were read off
# live crew panes on 2026-07-20 (not quoted from a doc — swept with capture-pane
# across every session on the host):
#   turn-end summary:  "✻ Crunched for 7m 56s · 1 shell still running"  (three
#                      different agents, four occurrences)
#   in-turn status:    "⏵⏵ bypass permissions on · 1 shell · esc to interrupt"
#                      (one agent, mid-flight)
# The first is the one that matters: it is printed exactly when the turn has
# ENDED, which is the moment the whole tier currently books as "finished".
_SHELLS_DONE = re.compile(r"(\d+)\s+shells?\s+still running")
_SHELLS_LIVE = re.compile(r"·\s*(\d+)\s+shells?\s*·")


def running_shells(screen: str) -> int | None:
    """How many background shells does this agent still own? None = NOT REPORTED.

    None IS NOT ZERO, and callers must not print it as "none running". A pane
    that is not showing the runtime's chrome (a bare shell, a scrolled view, a
    second runtime that has no such indicator) reports nothing, and "I could not
    see" is a different fact from "there are none". Collapsing those two is the
    defect this reader exists to close, one level up — turn-end silently booked
    as task-end — so it is not repeated inside the reader for it.

    Tail-only, for the reason every predicate in this file is: one agent's own
    pane contained the sentence "The pane shows 1 shell still running and a com…"
    while that agent ran no background shell at all. That is an agent TALKING
    about the state, and a whole-screen search would have read it as being in it.
    """
    tail = _tail(screen)
    for pat in (_SHELLS_DONE, _SHELLS_LIVE):
        m = pat.search(tail)
        if m:
            return int(m.group(1))
    return None


# --- what is in the input box? ----------------------------------------------

# ABSENT is not EMPTY and UNKNOWN is not EMPTY. Three different not-a-queue
# answers, kept apart on purpose — collapsing "I did not see an input box",
# "the box was empty" and "the box had text I could not classify" into one
# value is the shape of the bug this predicate exists to close.
INPUT_EMPTY = "empty"              # box on screen, nothing in it
INPUT_QUEUED = "queued"            # box on screen, REAL unsubmitted text in it
INPUT_PLACEHOLDER = "placeholder"  # box on screen, dimmed suggestion, buffer empty
INPUT_UNKNOWN = "?"                # box on screen with text, attributes stripped
INPUT_ABSENT = "no-box"            # no prompt line in the tail — does not apply


def input_state(screen: str) -> str:
    """Placeholder or queued? Requires an ATTRIBUTE-CARRYING capture (`-e`).

    Answers ONLY about the input box. It deliberately never says "the agent is
    idle": input state and run state are different questions, and the whole
    placeholder incident is one being read as the other.

    On a stripped capture with text in the box this returns UNKNOWN, never a
    guess. That is the point. The caller that dispatches on UNKNOWN would be
    typing into a buffer whose contents it cannot see — which is what happened.

    TRAILING BLANK ROWS ARE DROPPED FIRST (internal-ref), attribute-aware because
    this is the one predicate that must keep the RAW lines (it reads the dim
    attribute). tmux pads captures to pane height; on real stranded panes the
    prompt line sat above SEVEN rows of padding, fell outside the raw 8-line
    window, and this returned ABSENT — which work_state reads as
    box-does-not-apply and falls through to IDLE. Twelve deliberately-typed,
    never-submitted commands (bd ready, dispatch verdicts, a relayed Stiwi
    decision) printed `idle` that way in one coordination session, each needing
    a hand pane-capture to even find.
    """
    lines = screen.splitlines()
    while lines and not strip_attrs(lines[-1]).strip():
        lines.pop()
    for raw in reversed(lines[-_TAIL_LINES:]):
        plain = strip_attrs(raw).strip()
        if not plain or plain[0] not in _PROMPT_GLYPHS:
            continue
        # \xa0 (NBSP) is what the runtime actually puts after ❯; ordinary space
        # is what the older panes and the tests use. Both are the gutter.
        if plain[1:].strip("\xa0 \t"):
            after = raw[raw.find(plain[0]) + 1:]
            if _DIM in after:
                return INPUT_PLACEHOLDER
            # No dim AND no escapes at all on this line = the attributes were
            # stripped before we got here, so the absence of dim proves nothing.
            # Measured: a live runtime always colours its own prompt glyph.
            return INPUT_QUEUED if "\x1b" in raw else INPUT_UNKNOWN
        return INPUT_EMPTY
    return INPUT_ABSENT


# --- the dispatcher's question: who is FREE? --------------------

# The five answers, as printed. `?` is a first-class value, not a rounding of
# idle: "I could not tell" and "nobody is working" are different facts, and the
# whole cost of collapsing them is on record in this file (context_high, which
# reported False for every real pane and looked fine doing it).
#
# QUEUED joined them for the same reason (internal-ref): a pane whose UI is up,
# whose spinner is gone, and whose input box holds unsubmitted text is NOT idle.
# It is the internal-ref stall shape — and it used to print `idle` and land on the
# free list, where the next send-keys would concatenate onto the stuck text.
#
# WAITING joined them for the internal-ref measurement: 7 of 10 workers were sitting
# on blocking option-pickers SIMULTANEOUSLY, and every one printed `?`. That was
# honest — the ready UI is displaced by the picker, so `ui_up` is False and UNSURE
# is the correct answer to the question being asked. It was also useless: "I could
# not tell" and "this agent is stopped dead waiting for a person to answer it" are
# different facts, and the coordinator can only act on the second. Folding the
# actionable one into the unknown is the same collapse this file already carries
# three scars from (context_high, placeholder-vs-queued, None-is-not-zero).
BUSY, IDLE, WEDGED, UNSURE, QUEUED = "busy", "idle", "wedged", "?", "queued"
WAITING = "waiting"           # a picker is up and BLOCKING — needs a person, not a nudge
SATURATED = "saturated"       # PAST THE 400k CYCLE THRESHOLD — looks free, is a
                              # wall (internal-ref). 400k is a CYCLE point, not the
                              # ~1M context limit: past it, an agent must
                              # checkpoint its state to its bead and /clear before
                              # taking a new task. Naming it "% of limit" was a lie
                              # (Stiwi's correction) — 400k is not the ceiling.
AUTH_DEAD = "auth-dead"       # the runtime's LOGIN EXPIRED (internal-ref). The UI is
                              # up, the box is empty, and every API call fails —
                              # measured 2026-07-22: an operator re-login left all
                              # 9 crew like this, and every one printed `idle`, so
                              # feed_check counted them feedable and tend prompted
                              # into the dead panes. Not idle: BROKEN. The remedy
                              # is a relaunch (`st tend --reauth`) after the
                              # operator re-logs in — /login in the pane is an
                              # interactive browser OAuth flow nothing can drive.


def work_state(screen: str, ui_up: bool, awaiting: bool = False,
               limit_k: float = None, auth_dead: bool = False) -> str:
    """Is this agent WORKING right now? The verdict `st crew` never asked for.

    The predicates already existed — dispatch.py has refused sends into busy
    panes since #1 — but only the dispatcher ever consulted them, and only for
    one agent at a time. So an administrator planning a round had to run `st log`
    per agent and eyeball "Envisioning…" against an empty prompt (measured,
    sattler 2026-07-19, feeding five workers on a handoff's word). This is the
    same judgement, exposed as a value that can be printed for a whole roster.

    `ui_up` is the RUNTIME's answer to "is your UI on this screen" — passed in,
    not computed here, so triage stays runtime-blind (a second runtime has its own
    ready markers). It is what separates idle from unsure: a bare shell, a crashed
    runtime and a first-run consent prompt all show no in-flight marker, and NONE
    of them is an agent waiting for work. Without this check, "the pane is up and
    quiet" would print `idle` for a pane with nothing running in it at all —
    a dispatch target that would swallow the send into a shell.

    DELIBERATELY NOT is_live(): that also fails on DEAD_MARKERS, one of which is
    "Traceback". Agents print tracebacks constantly (running a failing test prints
    one), so keying free-ness on it would mark a genuinely free agent unsure right
    after it did its job — the wedged-marker mistake above, which cost a healthy agent a
    RESTART verdict, repeated one column over. Only the POSITIVE ready signal is
    consulted here.
    """
    if looks_wedged(screen):
        return WEDGED
    if mid_flight(screen):
        return BUSY
    # AUTH-DEAD next (internal-ref): `auth_dead` is the RUNTIME's answer, passed in
    # exactly like `ui_up` and `awaiting` — the login banner is runtime chrome and
    # this module knows no runtime's markers. After mid_flight on purpose (a pane
    # genuinely computing is busy; an auth-dead one cannot compute, so a spinner
    # means auth is fine), and BEFORE everything else: an auth-dead agent's picker,
    # empty box or saturation footer are all facts about a session that cannot make
    # an API call, and reporting any of them instead sends the coordinator to
    # answer a question or drive a cycle in a pane where nothing can run. Measured:
    # all 9 crew read `idle` through an entire login expiry, and tend's cycle
    # driver prompted a saturated auth-dead pane over and over — every prompt
    # failed with the same banner it could not see.
    if auth_dead:
        return AUTH_DEAD
    # AFTER mid_flight on purpose. A pane that is genuinely computing is BUSY even
    # if a picker's chrome is somewhere on it, and this ordering means the new
    # verdict can only ever convert a `?` — it cannot take an agent that used to
    # read busy and start calling it stalled. Additive by construction.
    #
    # `awaiting` is the RUNTIME's answer, passed in exactly like `ui_up`, because
    # picker chrome is runtime-specific and this module knows no runtime's markers.
    if awaiting:
        return WAITING
    if not ui_up:
        return UNSURE
    # The UI is up and nothing is in flight. That is NOT enough to say idle
    # (internal-ref): ask the input box, and believe it when it says it does not
    # know. Pass this function a capture taken WITH attributes or UNKNOWN is
    # the honest answer for every pane whose box has text in it.
    ins = input_state(screen)
    if ins == INPUT_QUEUED:
        return QUEUED
    if ins == INPUT_UNKNOWN:
        return UNSURE
    # The pane is up, quiet, and its box is empty — which reads as IDLE, the free
    # list, the next dispatch target. But an agent PAST the 400k cycle threshold
    # is not free: it must checkpoint + /clear before more work, so it is a wall
    # (internal-ref). Three agents sat here past the threshold (687k/562k/524k) for
    # fifteen hours, printed `idle`, and had work piled on that they could not
    # hold. The number was already on the pane ("/clear to save 687.8k tokens")
    # and already read by context_tokens_k; the tier just never asked whether it
    # was past the line. SATURATED converts what would be IDLE — it never takes an
    # agent that reads busy/queued/waiting, so like every state above it, additive.
    #
    # ONLY detectable here, and that is honest, not a gap: while a turn is in
    # flight the runtime replaces the "/clear to save" footer with the spinner, so
    # context_tokens_k returns None and a BUSY agent's depth is genuinely
    # unreadable from the pane. We do not guess it — a busy agent past the
    # threshold reads busy, and the number becomes available the moment it idles.
    threshold = CYCLE_THRESHOLD_K if limit_k is None else limit_k
    tokens = context_tokens_k(screen)
    if tokens is not None and tokens >= threshold:
        return SATURATED
    return IDLE


CTX_HINT = re.compile(r"/clear to save ([0-9.]+)k tokens")
CONTEXT_HIGH_TOKENS_K = 400.0
# The CYCLE THRESHOLD (Stiwi, internal-ref): past this many k tokens, an agent must
# checkpoint state to its bead and /clear BEFORE taking a new task. It is NOT the
# context limit (~1M) — it is the point at which cycling is cheaper than carrying
# on, so displaying depth as "% of limit" against 400k was a lie and was removed.
CYCLE_THRESHOLD_K = 400.0


def context_tokens_k(screen: str) -> float | None:
    """Claude Code's OWN context accounting, read off the pane.

    It offers "/clear to save 737.6k tokens" when context is worth clearing, and
    it reports the number. Returns None = UNKNOWN, never "low": while a turn is
    in flight the spinner replaces that footer. Callers must not read None as a
    green light — which is fine here, because mid_flight is checked first.
    """
    m = CTX_HINT.search(strip_attrs(screen))
    return float(m.group(1)) if m else None


def saturated(screen: str, limit_k: float = CYCLE_THRESHOLD_K) -> bool:
    """Is this agent PAST THE CYCLE THRESHOLD? (internal-ref)

    Named for the DECISION it drives: past 400k the agent must checkpoint state to
    its bead and /clear before taking a new task — unconditional on relatedness
    (Stiwi's rule). It is NOT a claim about the ~1M context limit. A deep agent
    does not just go slow: it loses earlier context, re-derives settled decisions,
    and misses constraints stated hundreds of thousands of tokens ago. Piling on
    produces worse output, not merely later output — so cycle first.

    None (footer not showing — a turn in flight) is NOT saturated: unknown is not
    over-limit, and mid_flight is judged first anyway.
    """
    tokens = context_tokens_k(screen)
    return tokens is not None and tokens >= limit_k


def context_high(screen: str, limit_k: float = CONTEXT_HIGH_TOKENS_K) -> bool:
    """Is this pane carrying enough context to be worth clearing?

    WAS: `len(screen.splitlines()) > 400` — screen length as a proxy. The proxy
    was honestly labelled, and it was still STRUCTURALLY INCAPABLE OF FIRING.
    Tmux.capture() runs `capture-pane -p` with no -S, so it returns the VISIBLE
    pane only: 24 lines on this fleet. 24 > 400 is never true. The CLEAR branch
    could only ever fire in a unit test that synthesised a 500-line screen — in
    production it was dead code, and `triage` was a nudge/refuse coin with a
    third face painted on.
    MEASURED on a live fleet: one agent carried 737.6k tokens — the textbook
    CLEAR case — and triage returned NUDGE. Every real pane returned
    context_high=False, always, for any input.
    This is the dead-branch class exactly ("a check incapable of one of its
    outcomes, and every one LOOKED FINE"), sitting in the file written to
    encode that lesson. The proxy was not too crude; it was measuring a
    different thing than the one it was named for.
    NOW: ask the runtime. Claude Code already counts the tokens and prints them.
    Verified to fire on real panes: 737.6k, 694.3k and 436.9k tokens.
    """
    tokens = context_tokens_k(screen)
    return tokens is not None and tokens >= limit_k


def unrelated(screen: str, new_work: str, threshold: float = 0.15) -> bool:
    """Keyword overlap. Crude and visible. Tune against real dispatches."""
    a = {w.lower() for w in new_work.split() if len(w) > 3}
    if not a:
        return False
    b = {w.lower() for w in screen.split() if len(w) > 3}
    return (len(a & b) / len(a)) < threshold


def triage(panes, target: str, new_work: str) -> Decision:
    """Order matters: cheapest and most certain checks first."""
    if not panes.exists(target):
        return Decision(Action.RESTART, "no session",
                        {"pane": target, "exists": False})

    # WITH attributes (internal-ref): dim is the only thing separating a
    # placeholder suggestion from queued-unsubmitted text, and `-p` alone drops
    # it. Every other predicate here strips internally, so this one capture
    # serves both views of the SAME instant.
    screen = panes.capture(target, attrs=True)
    lines = len(screen.splitlines())
    # Recorded on EVERY screen-based verdict, including the ones it does not
    # change (internal-ref ask 1). Whether a live background shell should block a
    # dispatch is a judgement nobody has ruled; that it must be VISIBLE is not.
    # A NUDGE that silently declined to look at a running build is the same class
    # of answer as a check that cannot fail — it reads clean either way, so the
    # operator cannot tell which happened. `shells=None` says "not reported".
    shells = running_shells(screen)

    # Report the marker from the TAIL — the same text the predicate judged on.
    # Searching the whole screen here would let the Decision name a marker that
    # is not the one that fired, which is an inspectable decision that lies.
    if looks_wedged(screen):
        return Decision(Action.RESTART, "wedged",
                        {"pane": target, "shells": shells,
                         "marker": next(m for m in WEDGED_MARKERS if m in _tail(screen))})

    if mid_flight(screen):
        return Decision(Action.REFUSE, "in-flight work",
                        {"pane": target, "shells": shells,
                         "marker": next(m for m in INFLIGHT_MARKERS if m in _tail(screen))})

    # The input box, BEFORE any nudge/clear decision (internal-ref). send-keys
    # does not replace a pane's input buffer, it APPENDS to it — so sending into
    # a box that already holds text produces one concatenated line that is
    # neither message. REFUSE covers both the fact and the doubt:
    #   queued  — there is real unsubmitted text in there (a live internal-ref
    #             stall, or a human mid-sentence). Either way, not ours to type
    #             over, and "un-stalling" it by hand is the defect, not the fix.
    #   unknown — the box has text and the capture carried no attributes, so
    #             placeholder and queued are the same bytes. Dispatching here is
    #             typing into a buffer we cannot see. REFUSE names the ambiguity
    #             instead of resolving it in whichever direction looks calmer.
    ins = input_state(screen)
    if ins in (INPUT_QUEUED, INPUT_UNKNOWN):
        return Decision(
            Action.REFUSE,
            "unsubmitted text in the input buffer" if ins == INPUT_QUEUED
            else "cannot tell a placeholder from queued input (no attributes)",
            {"pane": target, "input": ins,
             "attrs": "\x1b" in screen})

    # context_k is the number the operator needs to audit a CLEAR. Record it
    # even when it is None ("unknown" — the pane was not offering a hint), so a
    # NUDGE never silently means "I couldn't see".
    tokens = context_tokens_k(screen)
    hi = context_high(screen)
    # PAST THE CYCLE THRESHOLD -> CYCLE FIRST (Stiwi's rule, internal-ref).
    # UNCONDITIONAL on relatedness: past 400k, more work degrades the agent whether
    # or not it overlaps what it was doing, so there is no relatedness gate — an
    # earlier build gated on `unrelated` and a 687k agent handed RELATED work
    # slipped through as `healthy NUDGE`, which is how three agents stayed past the
    # threshold for fifteen hours. The remedy is CHECKPOINT-BEFORE-CLEAR: write
    # state to the bead FIRST, THEN /clear, THEN take the task — an auto-clear
    # would lose whatever was not saved, so the tier refuses and NAMES the remedy,
    # it does not perform it. `context_k` is the raw depth; there is deliberately
    # NO "% of limit" — 400k is a cycle point, not the ceiling, and framing depth
    # as a fraction of it was a lie.
    if tokens is not None and tokens >= CYCLE_THRESHOLD_K:
        return Decision(
            Action.CLEAR,
            "past the 400k cycle threshold — checkpoint, then clear",
            {"pane": target, "context_k": tokens, "shells": shells,
             "cycle_threshold_k": CYCLE_THRESHOLD_K,
             "remedy": "checkpoint state to the bead, THEN /clear (or hand off to "
                       "a fresh session), THEN take the task. Do NOT auto-clear — "
                       "it loses work that was not saved. Unconditional on "
                       "relatedness: past 400k, cycle before more work."})

    return Decision(Action.NUDGE, "healthy",
                    {"pane": target, "context_k": tokens, "shells": shells,
                     "screen_lines": lines, "context_high": hi})
