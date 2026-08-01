"""Ghost text is not stranded input — aegis-c6hli.

Claude Code draws a SUGGESTION in the input box: something it recommends you
say, TAB-completable, rendered faint. It differs from text a human typed and
never submitted by one rendering attribute, and `capture-pane -p` strips exactly
that attribute.

On 2026-08-01 a coordinator read a suggestion as stranded input and ran the
aegis-apz9 SOP on it — a turn and an interrupt into a working agent, spent on a
phantom. The SOP is right and expensive to lose: an injected line was once
laundered into execution by a coordinator pressing Enter. An SOP that fires
constantly on a benign condition is how the SOP gets ignored, and the failure
after that is the one that matters.

THE BYTES BELOW ARE MEASURED, NOT SYNTHESISED. Two independent live captures on
2026-08-01, twelve days after the aegis-x6xh corpus, same shape:
  gennaro, `tmux capture-pane -e -p -t shanty-sattler`
  sattler, an independent capture on another pane
plus the 2026-07-20 fleet sweep. Three specimens, one shape.
"""
from __future__ import annotations

import pytest

from shantytown import input_box, triage
from shantytown.tmux import CONTROL_KEYS, NullPanes


# --- the measured bytes -----------------------------------------------------

# shanty-sattler, live, 2026-08-01. The suggestion is wrapped in SGR 2 (faint);
# the prompt glyph is default-fg (39). VERBATIM.
GHOST_LINE = "\x1b[39m❯\xa0\x1b[2mhow's franklin doing on the governor?\x1b[0m"

# sattler's independent capture, same session, different pane. Same shape —
# which is the point: one specimen is an anecdote.
GHOST_LINE_2 = "\x1b[39m❯\xa0\x1b[2mtake the survival bands to sattler...\x1b[0m"

# The stranded shape, from the incident: typed, never submitted, carries no SGR.
TYPED_LINE = "\x1b[38;5;246m❯\xa0\x1b[39mfile the reconcile bead for ian"

# Box on screen, nothing in it. BOTH renderings occur — measured the same day on
# different panes — so neither may be hardcoded as "the" empty line.
EMPTY_LINE_246 = "\x1b[38;5;246m❯\xa0\x1b[39m"
EMPTY_LINE_39 = "\x1b[39m❯\xa0"

# THE DANGEROUS ONE. Typed text with a dim tail after it. I could not induce the
# runtime to render this (see the module docstring on triage._split_dim), and
# that is exactly why it is pinned: the classifier must not DEPEND on a negative
# I was unable to prove.
MIXED_LINE = "\x1b[39m❯\xa0file the\x1b[2m reconcile bead for ian\x1b[0m"

_RULE = "\x1b[38;5;244m" + "─" * 78
_MODE = "\x1b[39m  \x1b[38;5;246m⏵⏵ bypass permissions on\x1b[39m"


def _pane(line: str) -> str:
    return "\n".join([_RULE, line, _RULE, _MODE])


# --- the classification -----------------------------------------------------

def test_ghost_text_is_not_queued_input():
    """Both live specimens, and the verdict that stops the SOP firing."""
    for line in (GHOST_LINE, GHOST_LINE_2):
        assert triage.input_state(_pane(line)) == triage.INPUT_PLACEHOLDER
        assert triage.work_state(_pane(line), True) == triage.IDLE


def test_typed_text_is_queued_and_that_verdict_survives():
    assert triage.input_state(_pane(TYPED_LINE)) == triage.INPUT_QUEUED
    assert triage.work_state(_pane(TYPED_LINE), True) == triage.QUEUED


def test_both_empty_renderings_read_empty():
    for line in (EMPTY_LINE_246, EMPTY_LINE_39):
        assert triage.input_state(_pane(line)) == triage.INPUT_EMPTY


def test_UNDIMMED_TEXT_WINS_over_a_dim_tail():
    """The regression this bead exists to prevent, and the reason the classifier
    is not a substring test for the dim code.

    A line carrying BOTH typed text and a suggestion used to answer
    `placeholder`, which work_state reads as IDLE. A stranded — or INJECTED —
    line would hide behind a suggestion, re-opening the exact hole aegis-apz9
    closed. Real text present means QUEUED, whatever else is on the line.
    """
    assert triage.input_state(_pane(MIXED_LINE)) == triage.INPUT_QUEUED
    assert triage.work_state(_pane(MIXED_LINE), True) == triage.QUEUED


def test_dim_is_a_MODE_not_a_wrapper():
    """`\\x1b[2m` turns dim ON until something turns it off, so the split has to
    walk the line. `\\x1b[39m` (default fg) does NOT end dim — it appears
    mid-line constantly, and treating it as a reset would reclassify ghost text
    as typed, which is the false-positive direction that started this bead."""
    undim, dim = triage._split_dim("\x1b[2mall of this is dim\x1b[39m still dim\x1b[0m")
    assert undim.strip() == ""
    assert "still dim" in dim
    # 22 (normal intensity) is the standard partner of 2 and must also end it.
    undim, dim = triage._split_dim("\x1b[2mghost\x1b[22mtyped")
    assert undim == "typed" and dim == "ghost"


def test_a_stripped_capture_is_UNKNOWN_not_a_guess():
    """The refusal. Without attributes the deciding bit is gone, and answering
    anyway is what the whole module refuses to do."""
    for line in (GHOST_LINE, TYPED_LINE, MIXED_LINE):
        assert triage.input_state(triage.strip_attrs(_pane(line))) == triage.INPUT_UNKNOWN


# --- the command ------------------------------------------------------------

class _Panes(NullPanes):
    """A pane whose screen can be swapped, so a repaint can be modelled."""

    def __init__(self, screen: str):
        super().__init__(screen=screen)
        self.next_screen = None

    def capture(self, pane, history=0, attrs=False):
        return self.screen

    def control(self, pane, key):
        super().control(pane, key)
        if self.next_screen is not None:
            self.screen = self.next_screen


def test_show_prints_the_verdict_AND_the_evidence():
    rep = input_box.show(_Panes(_pane(GHOST_LINE)), "p")
    assert rep.verdict == input_box.GHOST
    # The evidence is the RAW line: the attribute is the whole argument, so a
    # verdict that dropped it would be the original bug wearing a new interface.
    assert "\x1b[2m" in rep.evidence


def test_clear_REFUSES_on_a_ghost_only_box():
    """There is nothing to clear in a suggestion — the buffer is already empty.
    A clear that appeared to work would teach the operator that suggestions are
    stalls, manufacturing the phantom workload this bead is about."""
    panes = _Panes(_pane(GHOST_LINE))
    rep = input_box.clear(panes, "p")
    assert rep.verdict == input_box.GHOST
    assert "REFUSED" in rep.detail
    assert panes.controls == []          # and it sent NOTHING


def test_clear_refuses_when_it_cannot_tell():
    panes = _Panes(triage.strip_attrs(_pane(TYPED_LINE)))
    rep = input_box.clear(panes, "p")
    assert rep.verdict == input_box.UNKNOWN
    assert "REFUSED" in rep.detail
    assert panes.controls == []


def test_clear_empties_a_typed_box_and_confirms_by_repaint():
    panes = _Panes(_pane(TYPED_LINE))
    panes.next_screen = _pane(EMPTY_LINE_246)      # the TUI redraws
    rep = input_box.clear(panes, "p")
    assert rep.verdict == input_box.EMPTY
    assert rep.changed is True
    assert ("p", "C-u") in panes.controls


def test_a_pane_that_never_repaints_is_NOT_reported_as_failure():
    """THE REPAINT TRAP, pinned. `capture-pane` returns the last painted FRAME,
    not the buffer: after a real C-u every capture still showed the original
    text because the TUI had not redrawn — the keys HAD landed.

    A verifier that trusts that frame reports "clear failed" forever and
    escalates a stall that does not exist. "The pane never repainted" and "the
    clear did not work" are different facts and must not collapse.
    """
    panes = _Panes(_pane(TYPED_LINE))              # next_screen stays None
    rep = input_box.clear(panes, "p")
    assert rep.changed is False
    assert "proves NOTHING" in rep.detail
    # and it tried a no-op nudge to shake a repaint loose, rather than giving up
    assert ("p", "C-a") in panes.controls


# --- the safety property ----------------------------------------------------

def test_no_path_can_emit_Enter_or_Tab():
    """The property that makes this command safe to hand a coordinator.

    Enter is the submit. Tab ACCEPTS the suggestion, so a "cleanup" path that
    typed Tab would inject the suggestion into the agent's turn — the aegis-apz9
    injection, performed by the tool built to prevent it. Neither is reachable,
    because Panes.control takes no text and its allowlist contains neither.
    """
    forbidden = {"Enter", "Tab", "C-m", "C-i", "KPEnter"}
    assert not (CONTROL_KEYS & forbidden)

    for screen, fn in [(_pane(TYPED_LINE), input_box.clear),
                       (_pane(GHOST_LINE), input_box.clear),
                       (_pane(GHOST_LINE), input_box.dismiss),
                       (_pane(EMPTY_LINE_246), input_box.dismiss)]:
        panes = _Panes(screen)
        panes.next_screen = _pane(EMPTY_LINE_246)
        fn(panes, "p")
        assert panes.sent == [], "input_box must never send TEXT"
        for _, key in panes.controls:
            assert key in CONTROL_KEYS
            assert key not in forbidden


def test_the_adapter_refuses_a_forbidden_key_by_construction():
    """Not a convention the callers keep — a refusal in the mechanism."""
    panes = NullPanes()
    for key in ("Enter", "Tab", "C-m", "rm -rf /"):
        with pytest.raises(ValueError):
            panes.control("p", key)


# --- the picker, which is not an input box at all ---------------------------

# aegis-crew-ian, live, 2026-08-01: a BLOCKING permission prompt. The picker
# marks its SELECTED OPTION with the same ❯ glyph the input box uses, so a
# classifier that only looks for the glyph calls this typed input.
PICKER_LINE = "\x1b[38;5;153m❯\x1b[39m \x1b[38;5;246m1. \x1b[38;5;153mYes\x1b[39m"


def test_a_blocking_picker_is_not_TYPED_input():
    """Found by running the live sweep, not by reasoning about it: ian reported
    TYPED "1. Yes" while its actual problem was that it needed an ANSWER.

    That is this bead's own false positive reproduced one pane over — a
    coordinator would run the stranded-input SOP on an agent nobody had typed
    into. work_state never showed it because it checks `awaiting` FIRST; --show
    had no such check.
    """
    panes = _Panes(_pane(PICKER_LINE))
    assert input_box.show(panes, "p", awaiting=True).verdict == input_box.PICKER
    # ...and without the runtime's answer it is exactly the trap: glyph + text.
    assert input_box.show(panes, "p", awaiting=False).verdict == input_box.TYPED


def test_clear_REFUSES_on_a_picker_and_sends_nothing():
    """C-u into a pane waiting on a decision is not a cleanup, it is a keystroke
    into a prompt somebody has to answer."""
    panes = _Panes(_pane(PICKER_LINE))
    rep = input_box.clear(panes, "p", awaiting=True)
    assert rep.verdict == input_box.PICKER
    assert "REFUSED" in rep.detail
    assert panes.controls == []


def test_the_tool_permission_prompt_is_a_BLOCKING_picker():
    """Pre-existing gap found while classifying input boxes (aegis-c6hli).

    The permission prompt is the commonest blocking picker in the fleet and its
    footer matches neither of the original two markers, so every agent stopped
    on one reported `?` — honest, unactionable, and precisely what aegis-qxc2
    added the predicate to fix. VERBATIM from a live pane.
    """
    from shantytown.runtime import asks_a_question, ClaudeRuntime
    screen = "\n".join([
        " Bash command",
        "   bd show it-1 2>&1 | head -80",   # path scrubbed: this repo is PUBLIC
        " This command requires approval",
        " Do you want to proceed?",
        " \u276f 1. Yes",
        "   2. Yes, and don\u2019t ask again",
        "   3. No",
        " Esc to cancel \u00b7 Tab to amend \u00b7 ctrl+e to explain",
    ])
    rt = ClaudeRuntime(NullPanes(), None, root=".")
    assert asks_a_question(rt, screen) is True
    assert triage.work_state(screen, True, awaiting=True) == triage.WAITING
    # ...and the input-box surface must not call the picker's ❯ typed input.
    assert input_box.show(_Panes(screen), "p", awaiting=True).verdict == input_box.PICKER
