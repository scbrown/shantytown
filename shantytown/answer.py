"""One primitive for "I could not look" and "I only saw part of it".

WHY THIS EXISTS. Eight open issues share one shape: a function that cannot
distinguish "no" from "could not look" returns the first one. `bd --json`
truncating 174 rows to 10 with empty stderr and exit 0; `st crew` omitting whole
statuses so Total minus Closed does not reconcile; `shanty ls` reporting "No
active sessions" against twelve live ones because it read the wrong socket;
doctor asserting "not installed" from a probe that could not have succeeded;
blank plates at exit 0; an empty registry read as a clean bill of health.

The repo already had the right instinct in FOUR separate shapes, each invented
at a different call site by whoever remembered:

    ContextUnavailable / RankUnavailable / EventsUnavailable / QuipuUnreachable
        — exceptions, for "I never got an answer"
    OK / BROKEN / CANNOT_TELL                    (selfcheck.py, roles.py)
        — a verdict triad, for health
    Registry.empty_note()                        (protocols.py)
        — a per-IMPL annotation of whether an empty answer is trustworthy
    FilesRegistry.all() raising on a missing dir
        — the same rule, hand-applied, in one adapter

THE GAP THOSE DO NOT COVER, AND WHY A NEW TYPE IS NEEDED RATHER THAN A FIFTH
CONVENTION. Every one of them is binary: either I answered, or I raised. None can
say **"here is a value, and it is incomplete"** — which is exactly what
truncation is. `bd --json` DID answer; it returned 10 real rows. No exception is
appropriate (nothing failed) and no verdict fits (it is not "broken"). The caller
gets a well-formed list that is quietly missing 164 entries, and every downstream
count, reconciliation and "is it empty?" is wrong with no signal anywhere. That
is the case `Answer` exists for, and it is why this is a value type and not a
fifth exception.

`empty_note()` is the closest existing thing and it is deliberately NOT replaced:
it annotates an IMPLEMENTATION ("my empties are never trustworthy"), decided once.
Completeness is a property of a SINGLE READ — the same adapter can answer
exhaustively at 09:00 and hit a row cap at 09:01. Per-impl and per-call are
different facts and both are worth having.

HOW IT MAKES THE BUG UNREPRESENTABLE. There is no public `.value`. Getting the
data out requires saying which of the two things you mean:

    ans.exact()              -> T, or RAISES PartialAnswer if the read was capped
    ans.at_least()           -> T, explicitly "I know this may be short"

So the failure mode — treating a truncated list as the whole world — cannot be
reached by forgetting something. It can only be reached by typing `at_least()`,
which is a claim the author makes on the record. Forgetting now costs a loud
exception at the call site instead of a silent wrong number in a report.

    counts = tracker.all_items()          # -> Answer[list[WorkItem]]
    len(counts.exact())                   # safe: raises if the read was capped
    for it in counts.at_least(): ...      # explicit: partial is acceptable here
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


class CouldNotLook(Exception):
    """I never got an answer. NOT "the answer was nothing".

    The unifying base for the exceptions this repo already grew independently
    (ContextUnavailable, RankUnavailable, EventsUnavailable, QuipuUnreachable).
    Those keep their names and their docstrings — they are caught by name in
    several places and renaming them would be churn for its own sake — but they
    now share an ancestor, so a caller that wants to treat every "could not look"
    the same way can write ONE except clause instead of remembering four.

    Callers map this to exit 2 (`cannot tell`), never exit 0.
    """


class PartialAnswer(Exception):
    """Raised by `Answer.exact()` when the read was capped or otherwise short.

    This is the whole enforcement mechanism. The caller asked for a number it
    intends to treat as the truth; the read cannot support that; saying so
    loudly at the call site is the entire point. Handle it by fixing the read
    (raise the cap, paginate) or by switching to `at_least()` and making the
    partial-ness visible in whatever you render.
    """


@dataclass(frozen=True)
class Answer(Generic[T]):
    """A value plus how it was obtained and whether the look was exhaustive.

    Construct through `complete()` / `capped()`, never directly — the classmethods
    are what force `how` to be recorded, and `how` is what makes a wrong answer
    diagnosable six weeks later instead of merely wrong.
    """

    _value: T
    how: str
    """What was actually done to obtain this — a command line, a query, a path.

    NOT a description of the value. This is the field that would have turned
    `shanty ls` printing "No active sessions" into "No active sessions (tmux -L
    shanty list-sessions)", at which point the wrong-socket bug reads itself off
    the screen. An answer that cannot say how it was measured cannot be audited.
    """

    complete: bool
    """True only if the observation covered the whole search space.

    An empty COMPLETE answer is a real finding: I looked everywhere and there is
    nothing. An empty INCOMPLETE answer is the bug this module exists to stop.
    """

    caveat: str | None = None
    """Why the look was not exhaustive — required when complete is False."""

    def __post_init__(self) -> None:
        # A capped answer that cannot say why it was capped is barely better than
        # a bare list: the operator sees "incomplete" and has nothing to act on.
        if not self.complete and not self.caveat:
            raise ValueError("an incomplete Answer must carry a caveat saying why")
        if not self.how:
            raise ValueError("an Answer must record how it was measured")

    @classmethod
    def complete_read(cls, value: T, *, how: str) -> "Answer[T]":
        """I looked at the whole search space. An empty value here MEANS empty."""
        return cls(_value=value, how=how, complete=True)

    @classmethod
    def capped(cls, value: T, *, how: str, caveat: str) -> "Answer[T]":
        """I got a value but could not see all of it — a row cap, a page limit, a
        filtered view. The value is REAL, just short."""
        return cls(_value=value, how=how, complete=False, caveat=caveat)

    def exact(self) -> T:
        """The value, on the claim that it is the whole truth.

        RAISES PartialAnswer if it is not. Use this anywhere the value feeds a
        count, a reconciliation, a diff, or an "is it empty?" branch — i.e.
        anywhere being short changes the conclusion rather than merely the
        display.
        """
        if not self.complete:
            raise PartialAnswer(
                f"read was incomplete ({self.caveat}); measured by: {self.how}"
            )
        return self._value

    def at_least(self) -> T:
        """The value, acknowledging it may be short.

        Deliberately verbose at the call site. Correct for display, sampling and
        "show me some" paths; wrong for anything that counts. If you find
        yourself reaching for this to silence `exact()`, the fix is usually the
        read, not the accessor.
        """
        return self._value

    def note(self) -> str | None:
        """One line for the operator, or None when the read was exhaustive.

        Rendered next to the value so a short answer LOOKS short on screen. This
        is what stops "0 sessions" and "0 sessions that I could see" from being
        the same pixels.
        """
        if self.complete:
            return None
        return f"INCOMPLETE: {self.caveat} (measured by: {self.how})"

    def map(self, fn) -> "Answer":
        """Transform the value, carrying provenance and completeness through.

        Without this, every `[a.name for a in ans.at_least()]` quietly drops the
        fact that the list was capped — the wrapper would be shed at the first
        comprehension and the bug would come straight back one layer up.
        """
        return Answer(
            _value=fn(self._value),
            how=self.how,
            complete=self.complete,
            caveat=self.caveat,
        )
