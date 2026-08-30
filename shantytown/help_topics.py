"""`st help <topic>` — where the rationale went when the pane texts were cut.

aegis-x6yoq. The recurring pane messages were essays because the WHY had nowhere
else to live: every reason anyone might need was pushed into every pane, every few
minutes, forever. That is the wrong trade. A rationale is read ONCE, by an agent
who wants it, at a moment of its choosing; an instruction is read every time.

So the instructions got short and the rationale came here. This is the other half
of that change and it is not optional: a one-line message pointing at `st help
handoff` is strictly worse than the essay if that command does not exist.
"""

from . import handoff_text

_HANDOFF = f"""\
HANDOFF / CYCLE — what to do when st tend says your context is high

  THE COMMAND
    Write your notes to a file, then ONE command:

      st cycle --self --checkpoint-file <notes>

    It posts the file as a comment on your checkpoint bead (or your active anchor),
    uses its first line as the reason, and records the request. `st tend` performs
    the actual stop + relaunch on its next pass. You keep working until it fires,
    and nothing is lost if it never does.

    Carrying graph context across the cycle:

      st cycle --self --checkpoint-file <notes> --quipu-node <name> --quipu-node <name>

    The nodes are named in your resume dispatch, so the fresh session starts by
    querying the graph instead of re-deriving what you just shed.

  DO NOT RUN /clear
    `/clear` drops the session out of bypass into MANUAL. You come back needing a
    human keystroke for every bash call — i.e. undispatchable — so the remedy needs
    its own remedy. Measured on a live agent: clearing a saturated
    agent fixed the context and created a second blocker, and a driver was handing
    that instruction out on a timer, fleet-wide, twelve times in one session.

    `st cycle` stops and relaunches instead, which RESTORES what /clear destroys:
    bypass, the MCP kit, skills, journaling, hooks, and your plate re-dispatch.

  IF IT IS REFUSED
    {handoff_text.refusal_note()}
    A refusal is not a failure and not a reason to reach for /clear. Your tree has
    uncommitted or unpushed work; commit or push it and the pending request is
    honoured on a later pass.

  WHY THIS PAGE EXISTS
    Until 2026-08-29 the fleet shipped six different answers to "your context is
    high", and half prescribed /clear. Which one you got depended on which surface
    flagged you: the cycle driver (400k, idle agents) taught the right thing, while
    the haul handoff (600k, mid-haul) taught /clear — and the haul path is the only
    one a BUSY agent can ever see. Agents following the fleet's own instructions
    did the wrong thing. All of them now come from one module, handoff_text.
"""

_HAUL = """\
HAUL — the self-feeding queue

  A haul is the set of READY beads already assigned to you. It advances itself:
  close one and the next is served at your stop. Nobody dispatches per bead.

  RELEASING AN ITEM — a bare status change does NOT stop the re-serve
    done            br close <id>
    gated           st defer <id> <bead|human|access|external|parked> --reason-file <f>
    not yours       br update <id> -a ""

  Why defer rather than just closing or unassigning: `defer` records the KIND of
  block and takes the bead OUT of the ready pool until you undo it. Clearing the
  assignee only RE-POOLS it — a still-ready bead is grabbed by the next idle agent,
  which is how one agent's "not mine" becomes another's surprise.

  BEING SERVED THE SAME BEAD AGAIN
    That is the re-serve rule, not a verdict on your work. An assigned, open, ready
    bead comes back until you release it. If you already judged it done, blocked or
    not yours, act on that judgement rather than re-reading the bead.

  CONTEXT HIGH MID-HAUL
    See `st help handoff`. Your haul resumes itself after a cycle.
"""

_INBOX = """\
INBOX — a pointer channel, not a document store

  Durable sends map to a tracker item titled 'inbox:' and are capped (~493 bytes of
  your text, after the '[from <you>] ' signature st adds). The cap is on BYTES, so
  non-ASCII (em dashes, arrows, checkmarks) costs more than it looks.

  OVER THE CAP — put the substance in a bead and send the pointer:

    br comments add <id> --file <notes>
    st inbox <who> -d 'see <bead-id>: <one-line gist>'

  WHY IT IS CAPPED
    A message that must survive a session death belongs in a bead, which is
    readable, greppable and permanent. The inbox exists to say WHERE to look. A
    long inbox message is a bead nobody can find later.

  BODIES THAT CONTAIN COMMANDS
    Write them to a file and use --file/--stdin. Prose in double quotes is expanded
    by the shell before st ever sees it: backticks and $(...) RUN, or are silently
    deleted while the tool reports success.
"""

TOPICS = {
    "handoff": _HANDOFF,
    "cycle": _HANDOFF,     # the two names people reach for, one page
    "haul": _HAUL,
    "inbox": _INBOX,
}


def render(topic: str) -> str | None:
    """The page, or None if there is no such topic."""
    return TOPICS.get((topic or "").strip().lower())


def index() -> str:
    names = sorted(set(TOPICS))
    return ("st help <topic>\n  topics: " + ", ".join(names) +
            "\n\n  handoff/cycle — what to do when your context is high\n"
            "  haul          — the self-feeding queue, and how to release an item\n"
            "  inbox         — the pointer channel and its cap\n")
