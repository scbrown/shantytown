"""WHO IS SPEAKING. One format, one place (aegis-5vxmz).

Stiwi, 2026-08-02: "crew comms could be more clear afa who is saying what, some
of those msgs could seem as they're from me. st should insert agent name into
all send keys."

send-keys types into the recipient's pane at the SAME prompt the human uses, so
an unattributed agent message is INDISTINGUISHABLE FROM THE OPERATOR. That is a
safety property, not a cosmetic one: Stiwi's word carries authority no agent has
— approvals, one-way doors, permission to push a public repo, standing
directives that override the reloaded instructions. An unsigned coordinator
message can be read as an operator approval, and the recipient has no way to
tell. Measured the same day: arnold's pane held "got the telegram, confirming
receipt — close 6te9n" — first person, naming the bead and the action, reading
exactly like Stiwi confirming the one fact aegis-6te9n was blocked on. It was
model-generated GHOST text. Attribution does not stop ghosts; it makes an
unsigned line a visible ANOMALY rather than the norm.

THE FORMAT LIVES HERE AND NOWHERE ELSE. `st inbox` shipped the prefix inline
first (eb26be0); the moment a second caller wanted it, two copies of a security
marker would be two things that can drift apart, and a marker readers learn to
trust must look the same every time or it teaches nothing.

WHY THIS IS A COMPOSER-SIDE FUNCTION AND NOT A `Panes.send(sender=...)` PARAM.
The obvious move is to prefix at the transport, where every send necessarily
passes. It is the wrong move here, and the repo already says why —
`ClaudeRuntime.start`'s own docstring: "Panes stays runtime-blind — it only ever
sees a finished string." Two of the eleven send sites do not carry prose at all:
`runtime.start` sends the LAUNCH COMMAND LINE and `_verify_live` sends the
one-key ANSWER to the folder-trust prompt. Prefixing a shell command breaks every
launch on the host; prefixing an answer answers a different question. A
transport that must be told "…but not this one" eleven times is not a chokepoint,
it is a chokepoint with an exception list — and the exception list is the thing
that has to be right either way. So the exception list is made EXPLICIT and
TESTED instead (tests/test_attribution_inventory.py), and the transport stays
dumb.

Naming the sender of an AUTOMATED push matters as much as naming an agent. A
`st tend` sweep writes prose in the imperative — "CYCLE NOW", "close-or-release"
— which is exactly the register an operator instruction arrives in.
"""
from __future__ import annotations

# The automated supervisor sweeps (notify.py). Every one of them is constructed
# in `st tend` and nowhere else (verified: cli.py's `_sweep` block is the only
# construction site of Notifier / CycleDriver / IdleFleetAlerter /
# BlockedStaleAlerter / StalledAlerter), so this constant is a fact about the
# code rather than a guess about the caller. If a sweep is ever driven from
# another command, this stops being true and the sender must come from the
# caller — attributing a `st crew`-driven push to `st tend` would be the exact
# wrong-name failure the module exists to prevent.
ST_TEND = "st tend"

# The quipu governed-workflow router (`st events`), the other non-agent sender.
ST_EVENTS = "st quipu-events"


def attribute(text: str, sender: str | None) -> str:
    """Prefix `text` with its sender — but ONLY when the sender is KNOWN.

    An unattributable send stays BARE rather than claiming a name it cannot
    support. That asymmetry is the whole design: a wrong name is authority
    laundering, which is the precise harm the prefix exists to prevent, so
    "cannot tell" must never be resolved into a plausible-looking name. A bare
    line is a line the reader must judge for themselves; a falsely signed one is
    a line they have been told not to.

    Idempotent by construction check, not by hope: a text that already carries
    the marker is returned unchanged, so a message passing two composers cannot
    end up "[from a] [from a] …". This matters because attribution happens at
    whichever layer owns the FORMAT — cli's inbox, dispatch's plan(), notify's
    push helpers — and those layers were not built with each other in view.
    """
    if not sender:
        return text
    if text.startswith("[from "):
        return text
    return f"[from {sender}] {text}"
