"""The ONE vocabulary for context-high handoff, and the only place it is worded.

Why this module exists (aegis-x6yoq, Stiwi 2026-08-29 direct):

    "the crew is still having trouble understanding how to switchover and handoff
     when st tend mentions the context is high, lets make that more obvious, maybe
     provide an st command that does the needful."

The command already existed. The problem was that the fleet shipped SIX different
answers to "your context is high", and half of them prescribed `/clear` — the
primitive aegis-3laza measured as actively harmful (it drops the session out of
bypass, so the agent returns undispatchable and the remedy needs its own remedy).
Which instruction an agent got depended on which surface flagged it: the
CycleDriver taught `st cycle --self` correctly, while the haul path — the only one
a BUSY agent can ever see — taught `/clear`. An agent obeying the fleet's own
instructions therefore did the wrong thing, and doing it right required ignoring
what it had just been told.

So the fix is not better wording in six places. It is ONE wording in one place:

  * every context-high remedy names `st cycle --self`, and no message anywhere
    instructs `/clear`;
  * the texts are POINTERS, not essays. These fire on a timer, into every pane,
    for the life of the fleet. Stiwi's second ask was to cut them: "can we shorten
    this ticket in file warning text its hilariously long and fires all the time."
    A message re-pushed every few minutes is read once and skipped forever after,
    which means a long one does not merely waste space — it trains agents to skip
    the line where the safety-critical sentence lives.

WHERE THE RATIONALE WENT. It is not deleted, it is relocated to `st help handoff`
and docs/handoff.md, written once and READ ON DEMAND. The rule for anything in
this module: if a sentence explains WHY, it belongs in the help topic; if it names
WHAT TO DO NEXT, it belongs here. The one exception is the `/clear` prohibition,
which stays inline everywhere because it is the mistake being prevented and an
agent that skips the pointer must still not reach for it.
"""

# The verb. Named once so a rename is a one-line change and cannot half-land.
CYCLE_CMD = "st cycle --self"

# The safety-critical clause. Kept SHORT enough to survive a skim, and repeated
# inline rather than pointed at, because it is the error being prevented.
# Keeps the WHY, in five words. The existing cycle tests assert "Do NOT run
# /clear", "bypass" AND "MANUAL", and they are right to: an instruction an agent
# cannot check is one it overrides under pressure, and "drops bypass" alone does
# not say what that costs. Shortening must not cost the reason — that is the
# difference between a pointer and a truncation.
NO_CLEAR = "Do NOT run /clear — it drops bypass into MANUAL."

HELP_POINTER = "Why/details: `st help handoff`."


def cycle_now(depth_k: float | int | None = None,
              threshold_k: float | int | None = None) -> str:
    """The context-high remedy. Two lines, naming the action and the command.

    Replaces a ~110-word essay that fired once per saturation episode per agent.
    """
    where = (f"{int(depth_k)}k, past the {int(threshold_k)}k cycle line"
             if depth_k and threshold_k else "past the cycle line")
    return (f"⚠ CONTEXT HIGH ({where}). CHECKPOINT, then cycle:\n"
            f"  bd comment <bead> --file <notes>  &&  {CYCLE_CMD} "
            f"--checkpoint-file <notes>\n"
            f"{NO_CLEAR} Cycle keeps bypass/MCP/skills/hooks and re-dispatches "
            f"your plate. Keep working until it fires. {HELP_POINTER}")


def haul_handoff(context_k: float | int, line_k: float | int) -> str:
    """Past the handoff line, mid-haul. The haul resumes itself after the cycle.

    This is the message a BUSY agent sees, and until now it was the one that said
    `/clear`. It is therefore the single most important string in this module.
    """
    return (f"⚠ HANDOFF: {int(context_k)}k, past the {int(line_k)}k line. Do not "
            f"start the next item. CHECKPOINT, then cycle:\n"
            f"  bd comment <bead> --file <notes>  &&  {CYCLE_CMD} "
            f"--checkpoint-file <notes>\n"
            f"{NO_CLEAR} Your haul resumes itself afterwards. {HELP_POINTER}")


def deep_context_hint() -> str:
    """One clause for the haul feed message — the nudge, not the procedure."""
    return (f"Context deep? Checkpoint + `{CYCLE_CMD}` first — not /clear; "
            f"the haul survives it.")


def coordinator_tag() -> str:
    """What a coordinator digest says a saturated agent is expected to do.

    Coordinator-facing, but it must name the SAME verb: a coordinator reading
    `/clear` here will tell an agent to /clear, which is how the wrong primitive
    survived a fix to the agent-facing half.
    """
    return f"checkpoints to its bead, then `{CYCLE_CMD}`"


def refusal_note() -> str:
    """What happens when the cycle is REFUSED, which agents currently panic about.

    A dirty or unpushed tree leaves the request PENDING — tend retries it every
    pass. Nothing is lost and nothing needs re-running; the fix is to commit or
    push. Without this line the agent sees a refusal, assumes the cycle is broken,
    and reaches for `/clear` — the exact outcome this module exists to prevent.
    """
    return ("If the cycle is REFUSED (dirty/unpushed tree): the request stays "
            "PENDING and tend retries it. Commit or push. Do not /clear.")
