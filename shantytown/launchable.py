"""Can this agent actually DO anything — the question `up` and `current` cannot ask.

WHY THIS MODULE EXISTS. Three cards on this fleet carried no
`dangerous`, so their agents launched in MANUAL MODE: a human keystroke required
to approve EVERY bash call. An unattended agent that needs a keystroke per
command cannot make progress by construction. It is not broken, not down, not
stale — it is *stopped*, and every surface we had reported it as `up` and
`current` and `busy`. One evening of that produced six coordinator
picker-answers across five agents, two agents blocked simultaneously, one agent
dead twice, and a permission gauntlet where each approval revealed the next.

The defect was visible the whole time — at the BOTTOM OF THE PANE, in the mode
line — and nothing looked there. So this module is two functions and one rule:

  launch_gaps(card)                what the CARD lacks. INTENT.
  observed_posture(plain, ui_up)   what the PANE shows. TRUTH.

Both, because they answer different questions and the incident needed both. The
card is what a supervisor will re-arm; the pane is what is actually running. They
disagree whenever a card is edited without a relaunch — the same launch-time rule
the settings column already exists for. Never report the card as if it were the
posture: that incident's fix was verified by the footer flipping, NOT by the
card content, and that distinction is the only reason we know the fix took.

The rule lives HERE, once, so `st crew` and `st tend --unretire` cannot come to
different conclusions about the same card — the same reason `_settings_verdict`
is shared between the roster column and `role set`.
"""
from __future__ import annotations
from typing import NamedTuple

from .workspace import unlaunchable

# --- what the CARD lacks (intent) -------------------------------------------

class Gap(NamedTuple):
    """One launch fault, in TWO lengths — deliberately.

    A roster line has room for two words and is read by someone scanning
    nineteen rows; a refusal has room for a paragraph and is read by exactly one
    person who has just been stopped. Rendering the paragraph on the roster
    buries it, and rendering the label at the refusal leaves the operator to
    guess the remedy. So the RULE is decided once and the wording is chosen by
    the caller — which is the only way `st crew` and `st tend --unretire` can be
    guaranteed to be talking about the same card.
    """
    short: str      # roster label, e.g. "no workspace"
    why: str        # the full sentence: what it costs, and the fix
    blocking: bool  # may a command REFUSE on this, or only say it loudly?


def launch_gaps(card) -> list[Gap]:
    """The card faults that make an unattended agent unable to do work.

    Deliberately NOT "is the card valid". A card can be perfectly well-formed,
    parse, launch, take a dispatch, and render `up` — and still describe an agent
    that cannot run a command. Two faults do that, and THE SAME THREE CARDS
    carried both:

      workspace  — respawned into whatever cwd the supervisor happens to have,
                   with none of its kit or charter.
      dangerous  — unset means MANUAL MODE: a keystroke per bash call.

    They arrived as separate incidents a day apart and it would be easy to treat
    them as separate checks. They are not: `retired = true` conceals ANY launch
    fault, because a card that is never launched never produces a symptom. So the
    faults accumulate silently on retired cards and surface together the moment
    one is re-armed — which is why this is a LIST, and why the next one belongs
    here rather than in a second gate somewhere else.

    The workspace half is workspace.unlaunchable() unchanged: it already carries
    the judgment that absent-but-clonable is fine and that workspace=None is a
    real election for a HAND launch. This function is the arming-time question,
    where no such election exists.

    ONLY ONE OF THE TWO MAY REFUSE, and the asymmetry is the considered part.
    A missing workspace is never elected on the supervisor path — nobody chose
    the cwd systemd happened to hand it — so it blocks. But `dangerous` is
    opt-in BY DESIGN in this harness (per-agent, never global, and pinned by
    tests), and an attended agent that wants a permission prompt on every call
    is making a real choice. A gate that refused it would override an election
    the harness deliberately offers, and would do it for every card that never
    set the field. So manual mode is said LOUDLY and does not stop the command:
    the operator running `--unretire` is present and can act on it, and `st crew`
    goes on saying it every time thereafter, which is the durable half. The
    defect here was never that manual mode is impossible to want — it
    was that choosing it accidentally was impossible to SEE.
    """
    gaps = []
    if why := unlaunchable(card):
        # "no workspace" is the commonest of unlaunchable's several verdicts, but
        # not the only one — the label follows the fault it actually found.
        gaps.append(Gap("no workspace" if not getattr(card, "workspace", None)
                        else "bad workspace", why, blocking=True))
    if not getattr(card, "dangerous", False):
        gaps.append(Gap("MANUAL MODE", (
            f"{card.name} carries no `dangerous`, so it launches in MANUAL MODE "
            f"— a human must approve EVERY bash call. An unattended agent that "
            f"needs a keystroke per command cannot make progress by "
            f"construction, and it reads `up`, `current` and `busy` the whole "
            f"time it is stopped dead. If that is deliberate, nothing here is "
            f"wrong. If it is not: `dangerous` on the card AND a relaunch (the "
            f"mode is read at launch)"), blocking=False))
    return gaps


# --- what the PANE shows (truth) --------------------------------------------

BYPASS = "bypass"      # observed: permission prompts are off
MANUAL = "MANUAL"      # observed: a live ready pane with NO bypass line
UNKNOWN = "?"          # not live, or nothing we can read — never rounded to either

# MEASURED off live crew panes 2026-08-01, verbatim:
#     ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents
#     ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ← for ag…
# It persists THROUGH a turn (second line above is a busy pane), which is what
# makes it readable on any capture rather than only on an idle one.
BYPASS_MARKER = "bypass permissions on"

# Read only the TAIL. The marker is ordinary English — this very file contains it
# twice — and a pane showing an agent's own output about permission modes must not
# read as a permission mode. The mode line is the last rendered row of a live
# pane; ten lines is slack for wrapping and for the blank rows a capture leaves
# under the footer, and nothing more.
FOOTER_LINES = 10


def observed_posture(plain: str, ui_up: bool) -> str:
    """BYPASS / MANUAL / UNKNOWN, from the stripped capture of a pane.

    DERIVED NEGATIVELY, on purpose. Only the bypass line has been measured here;
    the manual-mode footer has not, and a marker never observed passing is not a
    marker (the rule that already cost this repo two bad READY_MARKERS). So:
    a live ready pane that does not say bypass has permission prompts ON. That
    holds for every non-bypass mode Claude Code can be in — default, plan,
    accept-edits — without pinning a string for any of them, and it cannot rot
    when the runtime renames one.

    `ui_up` is required rather than inferred: a pane sitting on a trust dialog, a
    consent screen or a blocking picker has no mode line at all, and calling that
    MANUAL would be a fabricated measurement pointed at the wrong problem.
    """
    if not ui_up:
        return UNKNOWN
    tail = "\n".join((plain or "").splitlines()[-FOOTER_LINES:])
    return BYPASS if BYPASS_MARKER in tail else MANUAL
