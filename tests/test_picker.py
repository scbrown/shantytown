"""Reading and answering a BLOCKING picker — aegis-w30p2.

EVERY SPECIMEN IN THIS FILE IS VERBATIM off a live Claude Code pane (v2.1.220),
captured with `capture-pane -p -e` on 2026-08-01 and pasted with its escapes
intact. That is not ceremony. The whole defect being fixed is a coordinator
pattern-matching a picker by eye, and a test written against a picker somebody
REMEMBERED would be the same mistake with a green tick on it. Three shapes were
captured because the fleet shows three: the tool-permission prompt (much the
commonest), AskUserQuestion, and the folder-trust dialog.

Paths are the throwaway capture workspace — no fleet pane was driven to a prompt
to make this file, and no fleet pane was answered by it.
"""
from __future__ import annotations

import pytest

from shantytown import picker
from shantytown.tmux import CONTROL_KEYS, OPTION_KEYS, NullPanes


# --- the specimens ----------------------------------------------------------

# A Bash tool-permission prompt. THE ONE THAT MATTERS MOST: it is the commonest
# blocking picker in the fleet, and its option 2 carries the approved command
# INSIDE the option text — which is exactly why option 2 cannot be learned.
PERMISSION = "\n".join([
    "\x1b[38;5;153m" + "─" * 60,
    "\x1b[39m \x1b[1m\x1b[38;5;153mBash command\x1b[0m",
    "",
    "   curl -sS -m 5 https://example.com/ | head -3",
    "   \x1b[38;5;246mFetch\x1b[39m \x1b[38;5;246mexample.com\x1b[39m "
    "\x1b[38;5;246mand\x1b[39m \x1b[38;5;246mdisplay\x1b[39m",
    "",
    " This command requires approval",
    "",
    " Do you want to proceed?",
    " \x1b[38;5;153m❯\x1b[39m \x1b[38;5;246m1.\x1b[39m \x1b[38;5;153mYes\x1b[39m",
    "   \x1b[38;5;246m2.\x1b[39m Yes, and don’t ask again for: "
    "curl -sS -m 5 https://example.com/",
    "   \x1b[38;5;246m3.\x1b[39m No",
    "",
    " \x1b[38;5;246mEsc\x1b[39m \x1b[38;5;246mto\x1b[39m \x1b[38;5;246mcancel\x1b[39m "
    "\x1b[38;5;246m·\x1b[39m \x1b[38;5;246mTab\x1b[39m "
    "\x1b[38;5;246mto\x1b[39m \x1b[38;5;246mamend\x1b[39m",
    "", "", "",   # tmux pads a capture to the pane height
])

# AskUserQuestion. Two things here exist in no other specimen and both broke an
# earlier cut of the parser: per-option DESCRIPTION lines, and an option BELOW a
# horizontal rule (the "Chat about this" escape hatch).
ASKUSER = "\n".join([
    "\x1b[38;5;246m\x1b[49m" + "─" * 60,
    "\x1b[38;5;16m\x1b[48;5;153m ☐ Indentation \x1b[39m\x1b[49m",
    "",
    "\x1b[1m\x1b[38;5;231mDo you prefer tabs or spaces for indentation?\x1b[0m",
    "",
    "\x1b[38;5;153m❯\x1b[39m \x1b[38;5;246m1.\x1b[39m \x1b[38;5;153mTabs\x1b[39m",
    "     \x1b[38;5;246mUse tab characters for indentation\x1b[39m",
    "  \x1b[38;5;246m2.\x1b[39m Spaces",
    "     \x1b[38;5;246mUse\x1b[39m \x1b[38;5;246mspace\x1b[39m "
    "\x1b[38;5;246mcharacters\x1b[39m",
    "  \x1b[38;5;246m3.\x1b[39m \x1b[38;5;246mType\x1b[39m "
    "\x1b[38;5;246msomething.\x1b[39m",
    "\x1b[38;5;246m" + "─" * 60,
    "\x1b[39m  4. Chat about this",
    "",
    "\x1b[38;5;246mEnter\x1b[39m \x1b[38;5;246mto\x1b[39m "
    "\x1b[38;5;246mselect\x1b[39m \x1b[38;5;246m·\x1b[39m "
    "\x1b[38;5;246m↑/↓\x1b[39m \x1b[38;5;246mto\x1b[39m "
    "\x1b[38;5;246mnavigate\x1b[39m",
    "",
])

# The folder-trust dialog. Its question WRAPS, and the line directly above its
# options is a hyperlink reading "Security guide" — the specimen that killed
# "the prompt is the nearest non-blank line above the options".
TRUST = "\n".join([
    "\x1b[38;5;220m" + "─" * 60,
    "\x1b[39m \x1b[1m\x1b[38;5;220mAccessing\x1b[0m \x1b[1m\x1b[38;5;220mworkspace:\x1b[0m",
    "",
    " \x1b[1m/tmp/scratchpad/spec\x1b[0m",
    "",
    " Quick safety check: Is this a project you created or one you trust? "
    "If not, take a moment to review what's in this",
    " folder first.",
    "",
    " Claude Code'll be able to read, edit, and execute files here.",
    "",
    " \x1b[38;5;246m\x1b]8;id=zax;https://code.claude.com/docs/en/security\x1b\\"
    "Security guide\x1b[39m\x1b]8;;\x1b\\",
    "",
    " \x1b[38;5;153m❯\x1b[39m \x1b[38;5;246m1.\x1b[39m \x1b[38;5;153mYes,\x1b[39m "
    "\x1b[38;5;153mI\x1b[39m \x1b[38;5;153mtrust\x1b[39m \x1b[38;5;153mthis\x1b[39m "
    "\x1b[38;5;153mfolder\x1b[39m",
    "   \x1b[38;5;246m2.\x1b[39m No, exit",
    "",
    " \x1b[38;5;246mEnter\x1b[39m \x1b[38;5;246mto\x1b[39m "
    "\x1b[38;5;246mconfirm\x1b[39m \x1b[38;5;246m·\x1b[39m "
    "\x1b[38;5;246mEsc\x1b[39m \x1b[38;5;246mto\x1b[39m \x1b[38;5;246mcancel\x1b[39m",
])


# --- reading ----------------------------------------------------------------

def test_the_permission_prompt_reads_its_options_verbatim():
    """The command being approved must survive into the output.

    An operator approving `curl …` needs to see `curl …`, not "a Bash command".
    """
    q = picker.parse(PERMISSION)
    assert q.prompt == "Do you want to proceed?"
    assert [o.n for o in q.options] == [1, 2, 3]
    assert q.options[0].text == "Yes"
    assert q.options[0].selected is True
    assert q.options[2].text == "No"
    # VERBATIM, apostrophe and all — this is the text a coordinator reads before
    # deciding, so a normalised or truncated version is a different decision.
    assert q.options[1].text == (
        "Yes, and don’t ask again for: curl -sS -m 5 https://example.com/")
    assert any("curl -sS -m 5 https://example.com/" in c for c in q.context)
    assert "This command requires approval" in "\n".join(q.context)


def test_option_two_means_something_different_on_every_specimen():
    """The bead's central claim, as an assertion rather than an anecdote.

    Three real pickers from one evening. A coordinator who has learned that "2"
    is yes-and-remember types 2 at the trust dialog and answers "No, exit". This
    is the reason `ask` machine-reads the options instead of printing a capture.
    """
    twos = {picker.parse(s).option(2).text
            for s in (PERMISSION, ASKUSER, TRUST)}
    assert len(twos) == 3
    assert "No, exit" in twos
    assert "Spaces" in twos


def test_an_option_below_a_horizontal_rule_is_still_an_option():
    """AskUserQuestion draws a rule between the real options and "Chat about
    this", so a scan that stops at the first rule loses option 4 — and then
    `st answer <agent> 4` refuses an option that is really on the screen.

    A rule is also what DELIMITS the block above, so it cannot simply be ignored
    either; it ends the header, never the option list.
    """
    q = picker.parse(ASKUSER)
    assert [o.n for o in q.options] == [1, 2, 3, 4]
    assert q.option(4).text == "Chat about this"


def test_option_descriptions_attach_but_the_footer_does_not():
    """FOUND BY RUNNING IT, on all three specimens at once: with no indent test
    the LAST option swallowed the picker's key hints, and the permission prompt
    rendered as `3. No — Esc to cancel · Tab to amend`.

    Presenting the footer as the meaning of an option, on the command whose only
    job is to stop an operator misreading an option, is the bug being shipped
    inside its own fix.
    """
    q = picker.parse(ASKUSER)
    assert q.option(1).detail == "Use tab characters for indentation"
    assert q.option(4).detail == ""
    assert "Enter to select" in q.footer

    perm = picker.parse(PERMISSION)
    assert all(o.detail == "" for o in perm.options)
    assert "Esc to cancel" in perm.footer


def test_the_prompt_is_the_question_not_the_nearest_line():
    """The folder-trust dialog puts a "Security guide" link directly above its
    options and its actual question five lines further up, wrapped."""
    q = picker.parse(TRUST)
    assert "Is this a project you created or one you trust?" in q.prompt
    assert "Security guide" not in q.prompt
    # ...and the block is printed VERBATIM AND IN ORDER, so the wrapped second
    # half stays under the first instead of being orphaned by lifting the prompt
    # line out of the middle of it.
    joined = "\n".join(q.context)
    assert joined.index("one you trust?") < joined.index("folder first.")
    assert "Security guide" in joined


def test_prose_that_looks_like_options_is_not_a_picker():
    """`  2. Spaces` is a line agents write all day. The guard is that a picker
    is a COMPLETE run from 1, anchored at the bottom of the pane — this file's
    own specimens are the hazard, since a pane displaying them would otherwise
    report itself blocked (the trap every marker in runtime.py documents)."""
    prose = "\n".join([
        "I looked at three options for the parser:",
        "  2. match on the glyph",
        "  3. match on the footer",
        "and went with the third.",
    ])
    assert picker.parse(prose) is None


def test_an_empty_or_boxless_screen_reads_as_no_picker():
    assert picker.parse("") is None
    assert picker.parse("\x1b[39m❯\xa0\x1b[2mcat note.txt\x1b[0m") is None


# --- answering --------------------------------------------------------------

def _panes(screen: str) -> NullPanes:
    return NullPanes(screen)


class _Repainting(NullPanes):
    """A pane that shows the picker until a digit lands, then shows the ready UI.

    Models the measured behaviour: a bare digit selects AND confirms, so the
    picker is GONE on the next frame. A double that kept showing the picker would
    let `changed` pass for the wrong reason.
    """

    def option(self, pane: str, n: int) -> None:
        super().option(pane, n)
        self.screen = "\x1b[39m❯\xa0\n  ? for shortcuts"


def test_answering_selects_by_number_and_echoes_what_it_selected():
    panes = _Repainting(PERMISSION)
    res = picker.answer(panes, "p", 3, awaiting=True, agent="ellie",
                        journal=lambda *a: None)
    assert res.ok and res.changed
    assert res.option.text == "No"
    assert panes.picked == [("p", "3")]


def test_answering_REFUSES_a_pane_that_is_not_on_a_picker():
    """The refusal that protects a WORKING agent. A digit at an idle pane is not
    a no-op — it types into the input box, and the agent carries that stray
    character into whatever it submits next."""
    panes = _panes("\x1b[39m❯\xa0\n  ? for shortcuts")
    res = picker.answer(panes, "p", 1, awaiting=False, journal=lambda *a: None)
    assert not res.ok
    assert "REFUSED" in res.detail
    assert panes.picked == []


def test_answering_REFUSES_an_out_of_range_option():
    panes = _panes(PERMISSION)
    res = picker.answer(panes, "p", 4, awaiting=True, journal=lambda *a: None)
    assert not res.ok
    assert "no option 4" in res.detail
    assert "1, 2, 3" in res.detail          # says what IS on offer
    assert panes.picked == []


def test_answering_REFUSES_when_the_picker_could_not_be_read():
    """The runtime says a picker is up but no whole option run parsed. Sending
    the digit anyway would be answering by POSITION — the guesswork this command
    replaces."""
    panes = _panes("something is up but nothing numbered\n Esc to cancel")
    res = picker.answer(panes, "p", 1, awaiting=True, journal=lambda *a: None)
    assert not res.ok
    assert "could not be read" in res.detail
    assert panes.picked == []


def test_a_two_digit_option_is_refused_rather_than_half_sent():
    """Eleven options is not a failure of this module — it is a picker no single
    keystroke can address. Sending `1` at an `11` would approve option ONE."""
    many = "\n".join(
        [" Pick one:"] + [f"   {i}. option {i}" for i in range(1, 12)] +
        [" Enter to select"])
    q = picker.parse(many)
    assert q.option(11) is not None          # it IS read...
    panes = _panes(many)
    res = picker.answer(panes, "p", 11, awaiting=True, journal=lambda *a: None)
    assert not res.ok                        # ...and still refused
    assert "single keystroke" in res.detail
    assert panes.picked == []


def test_a_pane_that_never_repaints_is_not_reported_as_failure(monkeypatch):
    """`capture-pane` returns the last PAINTED frame. A verifier that reads once
    and believes it reports "the answer did not land" forever — and the operator
    then sends the digit AGAIN, into whatever the pane moved on to."""
    monkeypatch.setattr(picker, "_SETTLE_S", 0.05)
    panes = _panes(PERMISSION)               # never changes
    res = picker.answer(panes, "p", 1, awaiting=True, journal=lambda *a: None)
    assert res.ok is True                    # the key WAS sent
    assert res.changed is False              # ...and we cannot prove it landed
    assert "do NOT send it again" in res.detail.replace("Do NOT", "do NOT")


# --- the invariant ----------------------------------------------------------

def test_no_path_can_send_Enter_or_Tab():
    """The aegis-c6hli invariant, unchanged and re-asserted for the new verb.

    Enter submits; Tab ACCEPTS the ghost-text suggestion, which would put text
    the agent never wrote into its own turn. Neither is reachable — and adding
    `answer` did not need them, because a bare digit selects and confirms.
    """
    for forbidden in ("Enter", "C-m", "Tab", "C-i", "\n", "\r"):
        assert forbidden not in CONTROL_KEYS
        assert forbidden not in OPTION_KEYS
    assert OPTION_KEYS == frozenset("123456789")
    # The two allowlists stay DISJOINT: a digit is not an editing key (it types),
    # and an editing key is not an option. Collapsing them would make "no Enter,
    # no Tab" an assertion about a set nobody can read at a glance.
    assert not (CONTROL_KEYS & OPTION_KEYS)


def test_the_null_adapter_is_as_strict_as_the_real_one():
    """A double that accepted keys the shipped path refuses would let this file
    prove a refusal that does not exist."""
    panes = NullPanes("")
    for bad in (0, 10, -1):
        with pytest.raises(ValueError):
            panes.option("p", bad)
    with pytest.raises(ValueError):
        panes.control("p", "Enter")


def test_the_answer_is_journaled_before_it_is_sent():
    """WHO answered WHAT, on the fleet's one forensic log.

    An approval granted into another agent's pane is exactly the class of act
    that must not depend on someone remembering it: aegis-6hfmi spent a
    cross-session disagreement between two correct measurements establishing who
    un-retired ian, recoverable only because a person happened to recall it.
    """
    seen = []
    panes = _Repainting(PERMISSION)
    picker.answer(panes, "p", 2, awaiting=True, agent="ellie",
                  journal=lambda pane, text: seen.append((pane, text)))
    assert len(seen) == 1
    pane, line = seen[0]
    assert pane == "p"
    assert "agent=ellie" in line
    assert "<answer:2>" in line
    # the OPTION TEXT, not just the number — "answered 2" is unreadable a week
    # later, and the number alone is precisely what does not stay constant.
    assert "don’t ask again" in line
    assert "Do you want to proceed?" in line
