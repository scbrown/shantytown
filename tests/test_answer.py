"""The primitive that makes "could not look" unrepresentable as "no".

The load-bearing test is `test_empty_complete_and_empty_capped_are_different`.
Every one of the eight issues this type exists for reduces to those two cases
producing the SAME bytes: `bd --json` returning 10 of 174, `shanty ls` printing
"No active sessions" against twelve live, an empty registry read as a clean bill
of health. If those two ever compare equal again, the bug is back.
"""
from __future__ import annotations

import pytest

from shantytown.answer import Answer, CouldNotLook, PartialAnswer


def test_complete_read_is_exact():
    ans = Answer.complete_read([1, 2, 3], how="st crew")
    assert ans.exact() == [1, 2, 3]
    assert ans.at_least() == [1, 2, 3]
    assert ans.note() is None


def test_capped_read_refuses_to_be_exact():
    """The enforcement. A capped read cannot be consumed as the whole truth by
    forgetting anything — only by explicitly asking for at_least()."""
    ans = Answer.capped([1] * 10, how="bd list --json", caveat="row cap 10 of 174")
    with pytest.raises(PartialAnswer) as e:
        ans.exact()
    # The exception has to carry BOTH halves or it cannot be acted on: what was
    # short, and how it was measured.
    assert "row cap 10 of 174" in str(e.value)
    assert "bd list --json" in str(e.value)
    assert ans.at_least() == [1] * 10


def test_empty_complete_and_empty_capped_are_different():
    """THE WHOLE POINT. Both hold []. They must not behave alike."""
    real = Answer.complete_read([], how="tmux -L gt-ae5f35 list-sessions")
    unknown = Answer.capped([], how="tmux -L shanty list-sessions",
                            caveat="queried the wrong socket; server not found")

    assert real.exact() == []          # "there is genuinely nothing" — a finding
    with pytest.raises(PartialAnswer):  # "I could not see" — never a finding
        unknown.exact()

    assert real != unknown
    assert real.note() is None
    assert unknown.note() is not None


def test_incomplete_without_a_caveat_is_rejected():
    """An "incomplete" that cannot say why is barely better than a bare list."""
    with pytest.raises(ValueError, match="caveat"):
        Answer(_value=[], how="somewhere", complete=False, caveat=None)


def test_how_is_mandatory():
    """An answer that cannot say how it was measured cannot be audited."""
    with pytest.raises(ValueError, match="how it was measured"):
        Answer.complete_read([], how="")


def test_note_names_the_measurement():
    """The rendered line is what turns a wrong answer into a self-diagnosing one:
    "No active sessions (tmux -L shanty …)" reads the wrong-socket bug off the
    screen, where a bare "No active sessions" hides it."""
    ans = Answer.capped([], how="tmux -L shanty list-sessions",
                        caveat="no server on that socket")
    note = ans.note()
    assert "no server on that socket" in note
    assert "tmux -L shanty list-sessions" in note


def test_map_carries_incompleteness_through():
    """Without this the wrapper is shed at the first comprehension and the bug
    returns one layer up, which is exactly how it spread the first time."""
    capped = Answer.capped([{"n": 1}], how="bd list --json", caveat="row cap")
    names = capped.map(lambda rows: [r["n"] for r in rows])
    assert names.complete is False
    assert names.caveat == "row cap"
    assert names.how == "bd list --json"
    with pytest.raises(PartialAnswer):
        names.exact()


def test_map_preserves_completeness_for_a_full_read():
    full = Answer.complete_read([{"n": 1}], how="bd list --json")
    assert full.map(lambda rows: [r["n"] for r in rows]).exact() == [1]


def test_could_not_look_unifies_the_four_existing_exceptions():
    """These grew separately with the same reasoning written three times. One
    except clause must now catch them all — that is the deduplication."""
    from shantytown.protocols import (
        ContextUnavailable, EventsUnavailable, RankUnavailable,
    )
    from shantytown.quipu import QuipuUnreachable

    for exc in (ContextUnavailable, RankUnavailable, EventsUnavailable,
                QuipuUnreachable):
        assert issubclass(exc, CouldNotLook), exc
        # Still Exceptions, so every pre-existing `except <Name>` is unaffected.
        assert issubclass(exc, Exception), exc

    with pytest.raises(CouldNotLook):
        raise QuipuUnreachable("graph down")


def test_answer_is_frozen():
    """Provenance must not be editable after the fact — an Answer whose `how`
    can be rewritten downstream is not evidence of anything."""
    ans = Answer.complete_read([1], how="st crew")
    with pytest.raises(Exception):
        ans.how = "something else"  # type: ignore[misc]
