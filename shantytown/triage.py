"""triage — the part worth packaging. Everything else is plumbing.

Every rule here is encoded knowledge that was paid for. The comments say who paid.

The design constraint that outranks accuracy: DO NOT SHIP A CONFIDENT HEURISTIC
YOU CANNOT INSPECT. Every decision carries its inputs, so an operator can see why
it chose. `context_high` and `unrelated` are honest unknowns — crude and visible
beats clever and opaque.
"""
from __future__ import annotations
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

WEDGED_MARKERS = ("Traceback (most recent call last)", "^C^C", "[Process completed]")
INFLIGHT_MARKERS = ("esc to interrupt", "Running…", "Running...", "tokens · esc")


def looks_wedged(screen: str) -> bool:
    return any(m in screen for m in WEDGED_MARKERS)


def mid_flight(screen: str) -> bool:
    """An agent actively working. Sending now interrupts it.

    Gas Town's own nudge help says --mode immediate 'Send directly via tmux
    send-keys' and warns it interrupts. REFUSE is a real outcome.
    """
    return any(m in screen for m in INFLIGHT_MARKERS)


def context_high(screen: str, limit: int = 400) -> bool:
    """Crude on purpose: screen length as a proxy for context depth.

    This is a PROXY and it is named as one. It is not context usage; it is how
    much is on the pane. If we ever report it as context usage we have made the
    exact substitution that cost four agents a night (aegis-mt0r).
    """
    return len(screen.splitlines()) > limit


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

    if looks_wedged(screen):
        return Decision(Action.RESTART, "wedged",
                        {"pane": target, "marker": next(m for m in WEDGED_MARKERS if m in screen)})

    if mid_flight(screen):
        return Decision(Action.REFUSE, "in-flight work",
                        {"pane": target, "marker": next(m for m in INFLIGHT_MARKERS if m in screen)})

    hi = context_high(screen)
    if hi and unrelated(screen, new_work):
        return Decision(Action.CLEAR, "high context, unrelated",
                        {"pane": target, "screen_lines": lines, "overlap": "below threshold"})

    return Decision(Action.NUDGE, "healthy",
                    {"pane": target, "screen_lines": lines, "context_high": hi})
