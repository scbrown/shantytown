"""A placeholder suggestion is not queued input — internal-ref.

Both render as `❯ <text>`. The ONLY thing that separates them is a rendering
attribute, and `capture-pane -p` strips exactly that attribute. So the tier's
one liveness oracle was ambiguous in both directions at once:

  - a healthy IDLE agent showing a suggestion read as a stalled dispatch, and an
    administrator typed into its buffer to "un-stall" it (measured, sattler,
    2026-07-20 — the keystrokes went into a pane that had nothing wrong with it);
  - a REAL stall (send-keys landed, Enter never did — the internal-ref shape) reads
    as "just a suggestion, it's fine" and gets left wedged.

Every screen below is VERBATIM from a live pane on 2026-07-20, captured with
`capture-pane -p -e` across the whole 18-pane fleet. They are not synthesised:
the previous marker set in this repo was validated against one pane and matched
zero of nine, and that lesson is pinned in test_crew_work.py. Byte-level
fixtures are the point — the bug lives in the bytes.
"""
from __future__ import annotations

from shantytown import triage
from shantytown.tmux import NullPanes
from shantytown.triage import Action, triage as run_triage


# --- the measured bytes -----------------------------------------------------

# aegis-crew-ellie, idle, showing the dim suggestion of what to type next.
# `\x1b[2m` (SGR 2 = dim) wraps the text; the prompt glyph is default-fg.
PLACEHOLDER_LINE = "\x1b[39m❯\xa0\x1b[2mbd ready — pick the next item\x1b[0m"

# shanty-zia, live probe: `send-keys -l zzPROBEzz` with NO Enter, then captured.
# The prompt glyph is grey (38;5;246) and the typed text carries NO SGR at all.
QUEUED_LINE = "\x1b[38;5;246m❯\xa0\x1b[39mzzPROBEzz"

# shanty-weaver et al: box on screen, nothing in it.
EMPTY_LINE = "\x1b[38;5;246m❯\xa0\x1b[39m"

_RULE = "\x1b[38;5;244m" + "─" * 78
_MODE = ("\x1b[39m  \x1b[38;5;211m⏵⏵ bypass permissions on\x1b[38;5;246m"
         " (shift+tab to cycle) · ← for agents\x1b[39m")


def _pane(prompt_line: str) -> str:
    """A whole pane bottom, the way tmux hands it over."""
    return "\n".join([_RULE, prompt_line, _RULE, _MODE])


# --- the predicate ----------------------------------------------------------

def test_dim_is_a_placeholder_and_undimmed_is_queued_input():
    """The whole bug in two assertions. Same shape, opposite meaning, and the
    one bit that tells them apart survives only under `capture-pane -e`."""
    assert triage.input_state(_pane(PLACEHOLDER_LINE)) == triage.INPUT_PLACEHOLDER
    assert triage.input_state(_pane(QUEUED_LINE)) == triage.INPUT_QUEUED
    assert triage.input_state(_pane(EMPTY_LINE)) == triage.INPUT_EMPTY


def test_stripped_capture_is_unknown_not_empty_and_not_idle():
    """The negative control, and the reason this predicate exists.

    Strip the attributes off BOTH panes above and they become the same bytes.
    A predicate that answered anything other than UNKNOWN here would be
    answering from a screen that no longer contains the answer — confidently,
    and half the time wrongly."""
    for line in (PLACEHOLDER_LINE, QUEUED_LINE):
        stripped = triage.strip_attrs(_pane(line))
        assert "\x1b" not in stripped
        assert triage.input_state(stripped) == triage.INPUT_UNKNOWN
    # ...and the two really are indistinguishable once stripped. If this ever
    # fails, the runtime grew a plain-text tell and this module can get simpler.
    assert (triage.strip_attrs(_pane(PLACEHOLDER_LINE)).replace(
        "bd ready — pick the next item", "zzPROBEzz")
        == triage.strip_attrs(_pane(QUEUED_LINE)))


def test_an_empty_box_needs_no_attributes():
    """EMPTY is not ambiguous in the first place: there is nothing in the box to
    misread. A stripped capture still answers it, so the common case does not
    pay for the hard one."""
    assert triage.input_state(triage.strip_attrs(_pane(EMPTY_LINE))) == triage.INPUT_EMPTY
    assert triage.input_state("> \n  ? for shortcuts") == triage.INPUT_EMPTY


def test_no_input_box_is_absent_not_empty():
    """`ABSENT` is a third answer on purpose. "I saw an empty box" and "I never
    saw a box" are different facts, and only the first is evidence about the
    buffer."""
    assert triage.input_state("braino@vati:~$ ") == triage.INPUT_ABSENT
    assert triage.input_state("") == triage.INPUT_ABSENT


def test_only_the_tail_is_read():
    """Same rule as every other predicate here: a `> ` further up the screen is
    an agent QUOTING something, not an input box."""
    scroll = "\n".join(["> this is a markdown quote in the transcript"]
                       + [""] * 10) + "\n" + _pane(EMPTY_LINE)
    assert triage.input_state(scroll) == triage.INPUT_EMPTY


# --- the verdict it feeds ---------------------------------------------------

def test_queued_text_is_not_idle_and_never_lands_on_the_free_list():
    """The consequence that matters. An agent with unsubmitted text in its box
    used to print `idle`, which put it on `st crew`'s free list — and the next
    dispatch's send-keys APPENDS, producing one concatenated line that is
    neither message."""
    assert triage.work_state(_pane(QUEUED_LINE), ui_up=True) == triage.QUEUED
    assert triage.work_state(_pane(QUEUED_LINE), ui_up=True) != triage.IDLE


def test_a_placeholder_is_idle_because_the_buffer_really_is_empty():
    """The other direction, and the one that cost a healthy agent a faceful of
    keystrokes. A suggestion showing IS an idle agent — the fix for the false
    stall is to READ the pane correctly, not to type at it."""
    assert triage.work_state(_pane(PLACEHOLDER_LINE), ui_up=True) == triage.IDLE


def test_ambiguity_degrades_to_unsure_never_to_idle():
    """A stripped capture with text in the box is exactly the state the tier
    cannot reason about. `?` is the honest answer and it is already a
    first-class value here."""
    assert triage.work_state(
        triage.strip_attrs(_pane(QUEUED_LINE)), ui_up=True) == triage.UNSURE


def test_busy_still_beats_the_input_box():
    """Order check. A mid-flight agent that also has text queued is BUSY — the
    send would interrupt it either way, and BUSY is the older, stronger reason.
    The in-flight marker arrives colour-split word by word under `-e`, so this
    also pins that the marker match runs on the stripped view."""
    busy = _pane(QUEUED_LINE).replace(
        "for agents",
        "· \x1b[38;5;246mesc\x1b[39m \x1b[38;5;246mto\x1b[39m "
        "\x1b[38;5;246minterrupt\x1b[39m")
    assert triage.mid_flight(busy), "the marker did not survive per-word colouring"
    assert triage.work_state(busy, ui_up=True) == triage.BUSY


# --- and the dispatcher ------------------------------------------------------

def test_go_refuses_to_type_into_an_occupied_buffer():
    assert run_triage(NullPanes(screen=_pane(QUEUED_LINE)), "%1", "work").action \
        is Action.REFUSE


def test_go_refuses_when_it_cannot_tell_rather_than_assuming_placeholder():
    """REFUSE names the doubt instead of resolving it in whichever direction
    looks calmer. The refusal carries its inputs, so an operator can see that
    the reason was missing attributes and not a real queue."""
    d = run_triage(NullPanes(screen=triage.strip_attrs(_pane(QUEUED_LINE))),
                   "%1", "work")
    assert d.action is Action.REFUSE
    assert d.inputs["input"] == triage.INPUT_UNKNOWN
    assert d.inputs["attrs"] is False


def test_a_placeholder_does_not_block_a_dispatch():
    """The positive control. If dim panes refused too, this whole change would
    just be `st go` never dispatching to anyone — eight of the eighteen live
    panes measured were showing a placeholder."""
    assert run_triage(NullPanes(screen=_pane(PLACEHOLDER_LINE)),
                      "%1", "work").action is Action.NUDGE


def test_triage_asks_for_attributes():
    """The plumbing this all rests on. If triage ever goes back to a plain
    capture, every pane with text in its box silently becomes UNKNOWN and `st
    go` refuses the fleet — a loud failure, but this pins the cause."""
    seen = {}

    class _Recording(NullPanes):
        def capture(self, pane: str, history: int = 0, attrs: bool = False) -> str:
            seen["attrs"] = attrs
            return self.screen

    run_triage(_Recording(screen=_pane(EMPTY_LINE)), "%1", "work")
    assert seen["attrs"] is True
