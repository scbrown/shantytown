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
    out = NullRanker().weigh([_cand("graph::reachable")])
    assert out[0].weight == 0.0                    # rule-based order stands


def test_policy_ranker_weights_by_blast_radius():
    out = PolicyRanker(impact_fn=lambda _sym: 37).weigh([_cand("touch graph::reachable now")])
    assert out[0].weight == 37.0
    assert "blast radius 37" in out[0].why


def test_policy_ranker_skips_candidates_without_a_symbol():
    calls = []

    def impact(sym):
        calls.append(sym)
        return 5

    out = PolicyRanker(impact_fn=impact).weigh([_cand("fix the login timeout")])
    assert out[0].weight == 0.0
    assert calls == [], "no symbol -> the backend is never even asked"


def test_policy_ranker_raises_when_the_backend_cannot_look():
    def boom(_sym):
        raise RankUnavailable("hank unreachable")

    with pytest.raises(RankUnavailable):
        PolicyRanker(impact_fn=boom).weigh([_cand("graph::reachable")])
