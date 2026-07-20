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


def _tail(screen: str, n: int = _TAIL_LINES) -> str:
    return "\n".join(screen.splitlines()[-n:])


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


# --- the dispatcher's question: who is FREE? --------------------

# The four answers, as printed. `?` is a first-class value, not a rounding of
# idle: "I could not tell" and "nobody is working" are different facts, and the
# whole cost of collapsing them is on record in this file (context_high, which
# reported False for every real pane and looked fine doing it).
BUSY, IDLE, WEDGED, UNSURE = "busy", "idle", "wedged", "?"


def work_state(screen: str, ui_up: bool) -> str:
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
    if not ui_up:
        return UNSURE
    return IDLE


CTX_HINT = re.compile(r"/clear to save ([0-9.]+)k tokens")
CONTEXT_HIGH_TOKENS_K = 400.0


def context_tokens_k(screen: str) -> float | None:
    """Claude Code's OWN context accounting, read off the pane.

    It offers "/clear to save 737.6k tokens" when context is worth clearing, and
    it reports the number. Returns None = UNKNOWN, never "low": while a turn is
    in flight the spinner replaces that footer. Callers must not read None as a
    green light — which is fine here, because mid_flight is checked first.
    """
    m = CTX_HINT.search(screen)
    return float(m.group(1)) if m else None


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

    screen = panes.capture(target)
    lines = len(screen.splitlines())

    # Report the marker from the TAIL — the same text the predicate judged on.
    # Searching the whole screen here would let the Decision name a marker that
    # is not the one that fired, which is an inspectable decision that lies.
    if looks_wedged(screen):
        return Decision(Action.RESTART, "wedged",
                        {"pane": target,
                         "marker": next(m for m in WEDGED_MARKERS if m in _tail(screen))})

    if mid_flight(screen):
        return Decision(Action.REFUSE, "in-flight work",
                        {"pane": target,
                         "marker": next(m for m in INFLIGHT_MARKERS if m in _tail(screen))})

    # context_k is the number the operator needs to audit a CLEAR. Record it
    # even when it is None ("unknown" — the pane was not offering a hint), so a
    # NUDGE never silently means "I couldn't see".
    tokens = context_tokens_k(screen)
    hi = context_high(screen)
    if hi and unrelated(screen, new_work):
        return Decision(Action.CLEAR, "high context, unrelated",
                        {"pane": target, "context_k": tokens,
                         "limit_k": CONTEXT_HIGH_TOKENS_K,
                         "screen_lines": lines, "overlap": "below threshold"})

    return Decision(Action.NUDGE, "healthy",
                    {"pane": target, "context_k": tokens,
                     "screen_lines": lines, "context_high": hi})
