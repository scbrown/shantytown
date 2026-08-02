"""The blocking PICKER as a surface you can READ and ANSWER — aegis-w30p2.

WHY THIS EXISTS
`st input --show` (aegis-c6hli) can already tell a coordinator that an agent is
sitting on a PICKER. It cannot tell them WHAT IT ASKS, and it gives them no way
to answer it. So the coordinator dropped back to raw tmux — six times in one
evening across five agents:

    tmux -L gt-ae5f35 capture-pane -p -t aegis-crew-ellie | tail -12
    tmux -L gt-ae5f35 send-keys -t aegis-crew-ellie '2'

Every one of those hand-typed a SOCKET NAME and a PANE NAME, and three of them
were `aegis-crew-*` panes while most of the fleet is `shanty-*` — two naming eras
held in the head of someone who was, at that moment, also deciding whether to
approve another agent's shell command. The card already knows the pane.

READING THE OPTIONS IS THE SAFETY ARGUMENT, not a convenience. Option 2 does not
mean the same thing twice. Measured on live panes, 2026-08-01:

    2. Yes, and don't ask again for: curl -sS -m 5 https://example.com/
    2. No, exit
    2. Spaces

A coordinator who has learned "2 is yes-and-remember" and types 2 at the trust
dialog has just answered "No, exit". `capture-pane | tail -12` and a human
pattern-match is exactly how that happens; this module machine-reads them.

WHAT THIS MODULE WILL NOT DO
It will not answer on anybody's behalf. There is no --yes and no policy hook: a
permission prompt is a DECISION, and the whole value of it being a decision is
that a person made it (the aegis-apz9 rule). Making that one keystroke is the
goal here. Making it zero is a different and worse thing.

It also never sends Enter and never sends Tab. A bare digit SELECTS AND CONFIRMS
— measured, not assumed, on a live pane (see `answer`) — so the c6hli invariant
survives unchanged: no path in this module can submit an input buffer or accept
a ghost-text suggestion.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from . import triage

# How far up from the bottom of the pane to look for a picker at all. A picker
# is CHROME AT THE BOTTOM of the screen; the same words further up are an agent
# TALKING about one — the trap every text predicate in triage.py documents, and
# this file is a bigger target than most because "1. Yes" is ordinary prose.
# Measured against three live specimens: the whole block (rule through footer)
# was 13, 13 and 16 lines. 40 is generous and still bounded.
_WINDOW = 40

# A horizontal rule. Claude Code draws one ABOVE a picker block, which makes it
# the block delimiter this module uses — see _block(). It also draws them INSIDE
# the option list (the AskUserQuestion "Chat about this" escape hatch sits below
# one), so a rule must never end the option scan. That is not hypothetical: it
# is option 4 on a live specimen, and stopping at the rule would have made
# `st answer <agent> 4` refuse an option that is really there.
_RULE = re.compile(r"^[\s─━—–_=-]*$")

# `❯ 1. Yes` / `  2. No, exit`. The selection glyph is the SAME `❯` an input box
# uses (c6hli's false positive) — here it marks the selected OPTION.
_OPTION = re.compile(
    r"^(?P<mark>[ \t]*(?:❯|›|>)?[ \t]*)(?P<n>\d{1,2})\.[ \t]+(?P<text>\S.*?)[ \t]*$"
)

# A single keystroke can only address 1-9. Ten options is not a failure of this
# module, it is a picker that cannot be answered by digit at all, and saying so
# is better than sending `1` at a `10` and approving the wrong thing.
MAX_ADDRESSABLE = 9

# Same repaint budget and poll interval as input_box — same TUI, same trap.
_SETTLE_S = 3.0
_POLL_S = 0.15


@dataclass
class Option:
    n: int
    text: str
    selected: bool = False
    detail: str = ""      # the indented description line, when the picker draws one


@dataclass
class Question:
    prompt: str                                  # the interrogative line
    options: list[Option] = field(default_factory=list)
    context: list[str] = field(default_factory=list)  # verbatim lines above the prompt
    footer: str = ""                             # the picker's own key hints

    @property
    def numbers(self) -> list[int]:
        return [o.n for o in self.options]

    def option(self, n: int) -> Option | None:
        for o in self.options:
            if o.n == n:
                return o
        return None


def _lines(screen: str) -> list[str]:
    """Stripped, with trailing blank PADDING dropped.

    Both halves matter. Stripped because the runtime colours these blocks PER
    WORD — `Enter to select` arrives as
    `\\x1b[38;5;246mEnter\\x1b[39m \\x1b[38;5;246mto\\x1b[39m …` — so a substring
    or a column offset taken off the raw bytes is measuring the escapes, not the
    text. Trimmed because tmux pads a capture to the pane height, and a fixed
    window off the raw bottom then spends itself on blank rows: that is how
    kelly's answered picker hid behind five of them (aegis-qxc2).
    """
    out = triage.strip_attrs(screen).splitlines()
    while out and not out[-1].strip():
        out.pop()
    return out


def _option_run(lines: list[str], lo: int) -> list[tuple[int, Option]]:
    """The option lines of the LAST complete `1.`-through-`N.` run in the window.

    Requiring a run that starts at 1 is the guard against prose. Any line of the
    form `  2. Spaces` is option-shaped, and agents write numbered lists all day;
    an isolated match is far more likely to be a transcript than a picker. A
    contiguous ascending run ending at the bottom of the pane is not.
    """
    found: list[tuple[int, Option]] = []
    for i in range(lo, len(lines)):
        m = _OPTION.match(lines[i])
        if not m:
            continue
        found.append((i, Option(
            n=int(m.group("n")),
            text=m.group("text"),
            selected="❯" in m.group("mark") or "›" in m.group("mark"),
        )))
    if not found:
        return []
    # Walk BACKWARDS from the last match, taking N, N-1, … 1. Backwards because
    # the picker is the newest thing on screen; a stale numbered list further up
    # must not be able to capture the run.
    run: list[tuple[int, Option]] = []
    want = found[-1][1].n
    for idx, opt in reversed(found):
        if opt.n == want:
            run.append((idx, opt))
            want -= 1
        if want == 0:
            break
    if want != 0:            # never reached 1 — not a picker, or not a whole one
        return []
    run.reverse()
    return run


def _attach_details(lines: list[str], run: list[tuple[int, Option]]) -> None:
    """Give each option the indented description line under it, when there is one.

    AskUserQuestion draws one; the permission prompt does not. The lines are NOT
    option-shaped, so they cannot be confused with options — but printing them is
    the difference between `2. Spaces` and `2. Spaces — use space characters`,
    and this command exists so an operator does not have to guess what an option
    means.

    A DESCRIPTION IS INDENTED PAST ITS OPTION, and that test is load-bearing
    rather than cosmetic. FOUND BY RUNNING THIS: without it the last option
    swallowed the picker's FOOTER, and all three live specimens rendered as

        3. No    Esc to cancel · Tab to amend · ctrl+e to explain

    — the key hints presented as the meaning of the option, on the one command
    whose entire job is to stop an operator misreading what they pick. The
    footer is drawn at or left of the option's own indent in every specimen; a
    real description is further right. A blank line ends the option too: nothing
    below one belongs to it.
    """
    for pos, (idx, opt) in enumerate(run):
        nxt = run[pos + 1][0] if pos + 1 < len(run) else len(lines)
        indent = len(lines[idx]) - len(lines[idx].lstrip())
        for j in range(idx + 1, nxt):
            ln = lines[j]
            if not ln.strip():
                break
            if _RULE.match(ln) or _OPTION.match(ln):
                break
            if len(ln) - len(ln.lstrip()) <= indent:
                break
            opt.detail = ln.strip()
            break


def _block(lines: list[str], first_opt: int, lo: int) -> int:
    """Where the picker's block starts: the nearest rule ABOVE the options.

    Claude Code draws a rule over every one of these blocks — verified on all
    three live specimens (permission prompt, AskUserQuestion, folder-trust) — and
    it is a far better boundary than a fixed line count, because the block above
    the options is the part that VARIES: a one-line `Do you want to proceed?` in
    one case, four lines of command-plus-description in another.
    """
    for i in range(first_opt - 1, lo - 1, -1):
        if _RULE.match(lines[i]) and lines[i].strip():
            return i + 1
    return lo


def parse(screen: str) -> Question | None:
    """Read the picker off a pane, or None if there is no whole one to read.

    None is a REFUSAL, not an empty answer. Callers must render it as "could not
    read the question" and point at `st attach` — never as "there is no question",
    which is the could-not-tell-collapsed-into-a-verdict bug this codebase keeps
    paying for.
    """
    lines = _lines(screen)
    lo = max(0, len(lines) - _WINDOW)
    run = _option_run(lines, lo)
    if not run:
        return None
    _attach_details(lines, run)

    first_opt, last_opt = run[0][0], run[-1][0]
    start = _block(lines, first_opt, lo)

    # THE PROMPT: the last line above the options that ASKS something. Nearest
    # non-blank is not good enough and the folder-trust dialog proves it — the
    # line directly above its options is the words "Security guide" (a link),
    # while the actual question is five lines up. Anchoring on `?` finds the
    # question in all three specimens; the nearest-non-blank fallback only runs
    # for a picker that asks nothing, where any label beats an empty string.
    head = lines[start:first_opt]
    prompt = ""
    for ln in reversed(head):
        if "?" in ln:
            prompt = ln.strip()
            break
    if not prompt:
        for ln in reversed(head):
            if ln.strip() and not _RULE.match(ln):
                prompt = ln.strip()
                break

    # VERBATIM AND IN ORDER, prompt line included. An earlier cut lifted the
    # prompt OUT of this list, which read fine on the permission prompt and
    # mangled the folder-trust dialog: its question WRAPS across two rows, so
    # pulling row one out left the orphan "folder first." sitting between the
    # workspace path and a link. The block is what is on the screen; a command
    # built to stop an operator eyeballing a capture does not get to reorder it.
    context = [ln.rstrip() for ln in head
               if ln.strip() and not _RULE.match(ln)]
    footer = ""
    for ln in lines[last_opt + 1:]:
        if ln.strip() and not _RULE.match(ln) and not _OPTION.match(ln):
            footer = ln.strip()
    return Question(prompt=prompt, options=[o for _, o in run],
                    context=context, footer=footer)


@dataclass
class Answered:
    ok: bool
    option: Option | None = None
    changed: bool = True
    detail: str = ""


def _settle(panes, pane: str, before: list[str]) -> tuple[str, bool]:
    """Wait for the pane to actually REPAINT after the keystroke.

    The same trap input_box._settle documents: `capture-pane` hands back the last
    PAINTED frame, not the runtime's state, so a verifier that reads once and
    believes it reports "the answer did not land" forever. We poll for CHANGE.

    No C-a nudge here, unlike input_box. That nudge is a no-op in an input BUFFER;
    into a picker it is a keystroke at a prompt waiting on a decision, and this
    module does not get to press extra keys at one to make its own verification
    tidier. `changed=False` is returned honestly instead.
    """
    deadline = time.monotonic() + _SETTLE_S
    while time.monotonic() < deadline:
        screen = panes.capture(pane, attrs=True)
        if _lines(screen) != before:
            return screen, True
        time.sleep(_POLL_S)
    screen = panes.capture(pane, attrs=True)
    return screen, _lines(screen) != before


def answer(panes, pane: str, n: int, *, awaiting: bool,
           agent: str = "-", journal=None) -> Answered:
    """Select option `n` by NUMBER. Refuses rather than guessing.

    A BARE DIGIT SELECTS AND CONFIRMS — measured 2026-08-01 on a live Claude Code
    pane, twice: `1` at the folder-trust dialog trusted the folder and dropped
    straight to the ready UI, and `3` at a Bash permission prompt denied the call
    ("Interrupted · What should Claude do instead?"). No Enter was sent in either
    case, which is the whole reason this can exist without touching the c6hli
    allowlist: Enter and Tab remain unreachable from every path in shantytown.

    THE REFUSALS, in the order they fire:
      not awaiting   — the pane is not on a picker at all. A digit typed at an
                       idle agent lands in its INPUT BOX, and now it is carrying
                       a stray `2` that the next thing it submits will include.
      unreadable     — the picker is up but no whole option run could be read.
                       Answering a picker whose options we could not enumerate is
                       answering by position, which is the thing this replaces.
      out of range   — `n` is not an option that exists here.
      not a digit    — >9 options cannot be addressed by one keystroke.

    AUDITED BEFORE IT ACTS. An approval granted into someone else's pane is
    exactly the class of act that must not depend on anybody remembering they did
    it: aegis-6hfmi burned a cross-session disagreement between two correct
    measurements on "who un-retired ian", answerable only because a person
    happened to recall it. Journaled first, so an interrupted send still leaves
    its attempt.
    """
    if not awaiting:
        return Answered(False, detail=(
            "REFUSED: this pane is not on a picker. A digit sent to an agent "
            "that is idle or working does not select anything — it types a "
            "stray character into its INPUT BOX, which the agent then carries "
            "into whatever it submits next. Check with `st input --show`."))
    screen = panes.capture(pane, attrs=True)
    q = parse(screen)
    if q is None:
        return Answered(False, detail=(
            "REFUSED: a picker is up but its options could not be read, so "
            "there is no way to know what option "
            f"{n} says. Answering anyway would be answering by POSITION, which "
            "is the guesswork this command exists to remove. Use `st attach` "
            "and read it."))
    opt = q.option(n)
    if opt is None:
        return Answered(False, detail=(
            f"REFUSED: there is no option {n} here. This picker offers "
            f"{', '.join(str(i) for i in q.numbers)}."))
    if n > MAX_ADDRESSABLE:
        return Answered(False, detail=(
            f"REFUSED: option {n} cannot be reached by a single keystroke "
            f"(only 1-{MAX_ADDRESSABLE} can be). Use `st attach` — sending `1` "
            "at a two-digit option would select the wrong one."))

    if journal is None:
        from .tmux import journal as _default_journal
        journal = _default_journal
    journal(pane, f"<answer:{n}> agent={agent} option={opt.text!r} "
                  f"prompt={q.prompt!r}")

    before = _lines(screen)
    panes.option(pane, n)
    screen, changed = _settle(panes, pane, before)
    if not changed:
        return Answered(True, opt, changed=False, detail=(
            f"`{n}` was sent but the pane never repainted within {_SETTLE_S:g}s, "
            "so this frame proves NOTHING about whether it registered. Do not "
            "read that as failure and do NOT send it again — a second digit at a "
            "picker that did take the first is a keystroke into whatever came "
            "next. Look again, or attach."))
    return Answered(True, opt, detail=f"selected {n}. {opt.text}")
