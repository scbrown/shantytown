"""A pane BLOCKED ON A QUESTION is its own verdict, not `?` (internal-ref).

MEASURED (sattler, 2026-07-20): 7 of 10 workers sat on interactive option-pickers
SIMULTANEOUSLY and every one printed `?` in `st crew`. `?` was the correct answer
to the question being asked — the picker displaces the ready UI, so `ui_up` is
False — and it was useless: "I could not tell" and "this agent is stopped until a
person answers it" are different facts, and only the second can be acted on. Two
agents sat on ANSWERED pickers for over an hour because a by-hand pane sweep
missed them.

Every screen below is a REAL capture (`tmux capture-pane -p -e`) taken off the
live fleet on 2026-07-20, trimmed to the tail. Synthetic chrome is what let the
placeholder-vs-queued bug ship, so the fixtures here are bytes that actually
appeared on a pane.
"""
from __future__ import annotations

from shantytown import triage
from shantytown.runtime import ClaudeRuntime, asks_a_question


def _rt():
    # Markers and the predicate are class-level; no launcher wiring needed.
    return ClaudeRuntime.__new__(ClaudeRuntime)


def _verdict(screen: str) -> str:
    rt = _rt()
    plain = triage.strip_attrs(screen)
    return triage.work_state(screen, rt.shows_ready_ui(plain),
                             awaiting=asks_a_question(rt, plain))


# --- real panes -------------------------------------------------------------

# lowery, 2026-07-20 — an UNANSWERED picker. The footer arrives colour-split per
# word, which is why the predicate must see the stripped view.
LOWERY = (
    "    wrapper after all\n"
    "                    \x1b[38;5;153mNotes:\x1b[39m \x1b[3m\x1b[38;5;246mpress\x1b[0m \x1b[3m\x1b[38;5;246mn\x1b[0m\n"
    "\n"
    "\x1b[38;5;246m────────────────────────────────\n"
    "\x1b[39m  Chat about this\n"
    "\n"
    "\x1b[38;5;246mEnter\x1b[39m \x1b[38;5;246mto\x1b[39m \x1b[38;5;246mselect\x1b[39m \x1b[38;5;246m·\x1b[39m "
    "\x1b[38;5;246m↑/↓\x1b[39m \x1b[38;5;246mto\x1b[39m \x1b[38;5;246mnavigate\x1b[39m\n"
)

# kelly, 2026-07-20 — an ANSWERED picker, still blocking. Note the FIVE trailing
# blank lines: they pushed the marker out of a fixed 8-line tail and made this
# agent read `?` on the first cut of this feature — the exact agent the predicate
# exists to catch, missed by padding.
KELLY = (
    "\x1b[38;5;246mReady to submit your answers?\x1b[39m\n"
    "\n"
    "\x1b[38;5;153m❯\x1b[39m \x1b[38;5;246m1.\x1b[39m \x1b[38;5;153mSubmit\x1b[39m \x1b[38;5;153manswers\x1b[39m\n"
    "  \x1b[38;5;246m2.\x1b[39m Cancel\n"
    "\n\n\n\n\n"
)

# weaver, 2026-07-20 — ALSO printed `?`, and is NOT on a picker. Its ready marker
# ("shift+tab to cycle") is displaced by the background-shell indicator. This is
# the negative control: if the new verdict swallowed this too it would be
# repainting `?` rather than diagnosing it.
WEAVER = (
    "\x1b[38;5;244m────────────────────────────────\n"
    "\x1b[39m❯\xa0\x1b[2mnudge arnold to wire the wrapper\x1b[0m\n"
    "\x1b[38;5;244m────────────────────────────────\n"
    "\x1b[39m  \x1b[38;5;211m⏵⏵\x1b[39m \x1b[38;5;211mbypass\x1b[39m \x1b[38;5;211mpermissions\x1b[39m "
    "\x1b[38;5;211mon\x1b[38;5;246m ·\x1b[39m \x1b[38;5;44m3\x1b[39m \x1b[38;5;44mshells\x1b[39m\n"
)

# gennaro, 2026-07-20 — genuinely computing.
GENNARO = (
    "\x1b[38;5;174m✢\x1b[39m \x1b[38;5;174mGitifying… \x1b[38;5;246m(12s · ↓ 565 tokens · "
    "esc to interrupt)\x1b[39m\n"
    "\x1b[38;5;246m❯\xa0\x1b[39m\n"
    "  bypass permissions on (shift+tab to cycle)\n"
)


def test_an_unanswered_picker_is_waiting_not_unsure():
    assert _verdict(LOWERY) == triage.WAITING
    assert _verdict(LOWERY) != triage.UNSURE


def test_an_ANSWERED_picker_is_still_blocking():
    """The one that cost an hour. The question is answered; the pane is still
    stopped, because nobody pressed Enter. 'Answered' is not 'submitted'."""
    assert _verdict(KELLY) == triage.WAITING


def test_trailing_blank_lines_do_not_hide_the_picker():
    """kelly's real pane carried five blank lines under the picker. A fixed
    window off the raw bottom missed it — blank padding does not get to spend
    the tail."""
    assert _verdict(KELLY + "\n" * 6) == triage.WAITING


def test_a_pane_that_is_unsure_for_ANOTHER_reason_stays_unsure():
    """The negative control. weaver printed `?` too, and is not on a picker: its
    ready marker is displaced by the shells indicator. A verdict that captured
    this as well would be repainting `?`, not diagnosing it."""
    assert _verdict(WEAVER) == triage.UNSURE


def test_a_busy_pane_is_never_downgraded_to_waiting():
    """Ordering: mid_flight is checked FIRST, so the new verdict can only ever
    convert a `?`. It can never take an agent that read busy and call it stalled."""
    assert _verdict(GENNARO) == triage.BUSY
    assert triage.work_state(GENNARO, ui_up=True, awaiting=True) == triage.BUSY


def test_an_agent_QUOTING_picker_chrome_is_not_waiting():
    """This bead quotes the marker verbatim, so any agent reading it would match
    on a whole-screen search and report ITSELF stalled. Tail-only is what stops
    that — the same scar triage carries for classifying a healthy agent wedged
    because it printed a traceback."""
    talking = (
        'reading internal-ref, which quotes "Enter to select · n to add notes"\n'
        'and "Ready to submit your answers?" — I am merely discussing pickers.\n'
        + "\n" * 8
        + "\x1b[38;5;246m❯\xa0\x1b[39m\n"
        + "  bypass permissions on (shift+tab to cycle)\n"
    )
    assert _verdict(talking) == triage.IDLE


def test_default_is_off_so_the_verdict_cannot_appear_by_accident():
    """`awaiting` defaults False: every existing caller keeps its behaviour, and
    WAITING only ever appears because a runtime positively said so."""
    assert triage.work_state(LOWERY, ui_up=False) == triage.UNSURE


def test_a_runtime_that_cannot_answer_degrades_to_unsure_not_to_a_guess():
    """Pane-reading is optional (CodexRuntime implements neither this nor
    shows_ready_ui), so a runtime without the method must not crash a supervisor.
    It must also not answer 'not waiting' into a real verdict — the degraded path
    leaves `?`, the honest could-not-tell."""
    class Mute:
        def shows_ready_ui(self, screen):
            return False

    rt = Mute()
    assert asks_a_question(rt, triage.strip_attrs(LOWERY)) is False
    assert triage.work_state(LOWERY, rt.shows_ready_ui(LOWERY),
                             awaiting=asks_a_question(rt, LOWERY)) == triage.UNSURE


def test_a_stuck_FOLDER_TRUST_dialog_is_also_waiting():
    """A different dialog, the same consequence. `st new` auto-answers this one at
    launch, so seeing it in `st crew` means the launcher did NOT — and that agent
    is stopped dead. Reporting it `?` would be this same bug one dialog over."""
    trust = (
        "Do you trust the files in this folder?\n"
        "\n"
        "❯ 1. Yes, I trust this folder\n"
        "  2. No\n"
    )
    assert _verdict(trust) == triage.WAITING
