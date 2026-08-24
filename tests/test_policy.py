"""policy — the Ranker adapter. NullRanker (default, no backend) and PolicyRanker
(Hank blast radius via an injected impact fn — the _Fake pattern from
test_reactor). The load-bearing test is the exit-code honesty: a down backend
RAISES, it never returns an unweighted list pretending it looked.
"""
from __future__ import annotations

import pytest

from shantytown import workflow as wf
from shantytown.policy import NullRanker, PolicyRanker
from shantytown.protocols import RankUnavailable, WorkItem


def _cand(title=None):
    item = WorkItem("st-1", title, "in_progress", "ellie") if title else None
    return wf.Candidate("ellie", "worker", wf.AgentState.STOPPED, item=item)


def test_null_ranker_leaves_the_order_untouched():
    out = NullRanker().weigh([_cand("graph::reachable")]).exact()
    assert out[0].weight == 0.0                    # rule-based order stands


def test_policy_ranker_weights_by_blast_radius():
    out = PolicyRanker(impact_fn=lambda _sym: 37).weigh([_cand("touch graph::reachable now")]).exact()
    assert out[0].weight == 37.0
    assert "blast radius 37" in out[0].why


def test_policy_ranker_skips_candidates_without_a_symbol():
    calls = []

    def impact(sym):
        calls.append(sym)
        return 5

    out = PolicyRanker(impact_fn=impact).weigh([_cand("fix the login timeout")]).at_least()
    assert out[0].weight == 0.0
    assert calls == [], "no symbol -> the backend is never even asked"


def test_policy_ranker_raises_when_the_backend_cannot_look():
    def boom(_sym):
        raise RankUnavailable("yupana unreachable")

    with pytest.raises(RankUnavailable):
        PolicyRanker(impact_fn=boom).weigh([_cand("graph::reachable")])


# --- aegis-q0bzh: the partial RankUnavailable never covered -------------------
#
# `RankUnavailable` says "I could not reach the backend at all". It says nothing
# about the case that actually happens on every haul: PolicyRanker skips any
# candidate whose title carries no `mod::sym` token, so those keep weight 0 —
# indistinguishable from a real blast radius of zero. Ranking's version of an
# empty list that might mean "nothing found" or "never looked".
#
# Both directions, because a fix that marked EVERYTHING capped would pass a
# one-directional version of this and make the caveat meaningless.

def test_a_skipped_candidate_makes_the_answer_CAPPED_and_says_how_many():
    weighed = PolicyRanker(impact_fn=lambda _sym: 5).weigh(
        [_cand("touch graph::reachable now"), _cand("fix the login timeout")])

    assert not weighed.complete, "one candidate was never weighed; that is not complete"
    note = weighed.note()
    assert "1 of 2" in note, note
    assert "not asked" in note, "the caveat must say what weight 0 does NOT mean"
    # The value is still REAL and usable — capped means short, not wrong.
    assert len(weighed.at_least()) == 2


def test_weighing_every_candidate_is_COMPLETE_and_carries_no_note():
    weighed = PolicyRanker(impact_fn=lambda _sym: 5).weigh(
        [_cand("touch graph::reachable now"), _cand("also mod::other here")])

    assert weighed.complete
    assert weighed.note() is None, "a complete read must not manufacture a caveat"
    assert weighed.exact()[0].weight == 5.0


def test_the_null_ranker_is_COMPLETE_not_capped():
    """It weighs nothing BY DESIGN and there is no backend that could say more,
    so its unweighted answer means unweighted — the empty-that-really-is-empty."""
    weighed = NullRanker().weigh([_cand("no symbol here")])
    assert weighed.complete
    assert weighed.note() is None
    assert "no backend" in weighed.how


def test_every_answer_records_HOW_it_was_measured():
    """An answer that cannot say how it was obtained cannot be audited — the
    Answer contract enforces it, and these are the two impls that must satisfy it."""
    for weighed in (NullRanker().weigh([_cand("x")]),
                    PolicyRanker(impact_fn=lambda _s: 1).weigh([_cand("a::b")])):
        assert weighed.how, "an Answer must record how it was measured"
